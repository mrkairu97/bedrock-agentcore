"""Returns & Refunds Assistant: Streamlit chat UI with Cognito login and AgentCore Runtime."""

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

import boto3
import streamlit as st
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

REGION: str = "us-west-2"
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Workshop convenience: pre-fill the login form with the test user created in Part 5.
DEFAULT_EMAIL: str = "administrator@example.com"
DEFAULT_PASSWORD: str = "Workshop1!"

WELCOME_MESSAGE: str = (
    "Hello! I'm your Returns & Refunds Assistant. I can help you look up orders, "
    "check return eligibility, calculate refunds and answer policy questions. "
    "How can I help you today?"
)


# ---------------------------------------------------------------------------
# Configuration: read IDs from the files the earlier workshop steps wrote
# ---------------------------------------------------------------------------
# `agentcore deploy` creates this CloudFormation stack: AgentCore-<project>-<target>.
STACK_NAME: str = "AgentCore-AgentCoreProject-default"


def find_runtime_arn() -> str:
    """Return the deployed runtime ARN.

    agentcore/.cli/deployed-state.json is not reliable after a redeploy (the runtime can
    be replaced), so read the ARN from the stack outputs instead. Set AGENT_RUNTIME_ARN
    to override it.
    """
    override = os.environ.get("AGENT_RUNTIME_ARN")
    if override:
        return override
    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=STACK_NAME)["Stacks"][0]
    for output in stack.get("Outputs", []):
        if output["OutputKey"].startswith("ApplicationRuntimeCustomerAssistantAgentRuntimeArnOutput"):
            return output["OutputValue"]
    raise RuntimeError(f"No runtime ARN output found on stack {STACK_NAME}")


def load_config() -> dict[str, str]:
    """Read the Cognito IDs from cognito_config.json and look up the runtime ARN."""
    cognito = json.loads((PROJECT_ROOT / "cognito_config.json").read_text(encoding="utf-8"))
    return {
        "user_pool_id": cognito["user_pool_id"],
        # The UI needs a public app client that allows USER_PASSWORD_AUTH. The gateway's
        # machine-to-machine client (client_id) cannot sign users in.
        "client_id": cognito["ui_client_id"],
        "runtime_arn": find_runtime_arn(),
    }


# Cache so the CloudFormation lookup happens once per server process, not on every rerun.
@st.cache_resource
def get_config() -> dict[str, str]:
    return load_config()


CONFIG: dict[str, str] = get_config()

# Sign-in calls are unauthenticated by design, so this client needs no AWS credentials.
cognito = boto3.client("cognito-idp", region_name=REGION, config=Config(signature_version=UNSIGNED))

# Agent calls are signed with the AWS credentials of whoever runs this app.
# Long read timeout: the agent may call several tools before it answers.
agentcore = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(connect_timeout=10, read_timeout=120, retries={"max_attempts": 3, "mode": "standard"}),
)


# ---------------------------------------------------------------------------
# Cognito authentication
# ---------------------------------------------------------------------------
def start_session(email: str, tokens: dict[str, Any]) -> None:
    """Store tokens and create the per-login chat state."""
    st.session_state.tokens = tokens
    st.session_state.email = email
    # One session ID for the whole login, so AgentCore keeps the conversation context.
    # It must be at least 33 characters; a UUID is 36.
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]
    st.session_state.pop("challenge", None)


def sign_in(email: str, password: str) -> None:
    try:
        response = cognito.initiate_auth(
            ClientId=CONFIG["client_id"],
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
    except ClientError as err:
        code = err.response["Error"]["Code"]
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            st.error("Incorrect email or password.")
        else:
            st.error(f"Sign-in failed: {err.response['Error']['Message']}")
        return
    except BotoCoreError as err:
        st.error(f"Could not reach Cognito: {err}")
        return

    # First login with a temporary password: Cognito asks for a new one.
    if response.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
        st.session_state.challenge = {"session": response["Session"], "email": email}
    else:
        start_session(email, response["AuthenticationResult"])
    st.rerun()


def set_new_password(new_password: str) -> None:
    challenge = st.session_state.challenge
    try:
        response = cognito.respond_to_auth_challenge(
            ClientId=CONFIG["client_id"],
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=challenge["session"],
            ChallengeResponses={"USERNAME": challenge["email"], "NEW_PASSWORD": new_password},
        )
    except ClientError as err:
        # e.g. InvalidPasswordException when the password policy is not met.
        st.error(err.response["Error"]["Message"])
        return
    except BotoCoreError as err:
        st.error(f"Could not reach Cognito: {err}")
        return
    start_session(challenge["email"], response["AuthenticationResult"])
    st.rerun()


def sign_out() -> None:
    for key in ("tokens", "email", "session_id", "messages", "challenge"):
        st.session_state.pop(key, None)
    st.rerun()


def render_login() -> None:
    if "challenge" in st.session_state:
        st.subheader("Set a new password")
        st.caption("This is your first sign-in. Choose a new password to continue.")
        with st.form("new_password_form"):
            new_password = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Set New Password"):
                if new_password != confirm:
                    st.error("Passwords do not match.")
                else:
                    set_new_password(new_password)
        return

    st.subheader("Sign in")
    with st.form("login_form"):
        email = st.text_input("Email", value=DEFAULT_EMAIL)
        password = st.text_input("Password", value=DEFAULT_PASSWORD, type="password")
        if st.form_submit_button("Sign in"):
            sign_in(email.strip(), password)


# ---------------------------------------------------------------------------
# AgentCore Runtime
# ---------------------------------------------------------------------------
def chunk_to_text(chunk: str) -> str:
    """Turn one streamed chunk into display text.

    Try JSON first. A JSON string is returned without its quotes. Our Strands agent
    streams event objects, so for those we keep only the text deltas. Anything that is
    not JSON is returned as-is.
    """
    try:
        value = json.loads(chunk)
    except json.JSONDecodeError:
        return chunk
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "error" in value:
            raise RuntimeError(str(value["error"]))
        delta = value.get("event", {}).get("contentBlockDelta", {}).get("delta", {})
        return delta.get("text", "")
    return ""


def stream_agent_response(prompt: str, actor_id: str, session_id: str) -> Iterator[str]:
    """Invoke the deployed agent and yield the answer text as it streams in."""
    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=CONFIG["runtime_arn"],
        runtimeSessionId=session_id,
        # actor_id identifies the user (the part of the email before @).
        payload=json.dumps({"prompt": prompt, "actor_id": actor_id}).encode("utf-8"),
    )

    if "text/event-stream" not in response.get("contentType", ""):
        # Non-streaming reply: read the whole body.
        yield chunk_to_text(response["response"].read().decode("utf-8"))
        return

    after_tool_call = False
    for line in response["response"].iter_lines(chunk_size=10):
        if not line:
            continue
        text = line.decode("utf-8")
        if text.startswith("data: "):
            text = text[len("data: "):]
        # A tool call ends the text block before it, so separate the next block from it.
        if '"toolUse"' in text and '"contentBlockStart"' in text:
            after_tool_call = True
        out = chunk_to_text(text)
        if out:
            if after_tool_call:
                out = "\n\n" + out
                after_tool_call = False
            yield out


def friendly_error(err: Exception) -> str:
    if isinstance(err, ClientError):
        code = err.response["Error"]["Code"]
        if code == "ThrottlingException":
            return "The agent is receiving too many requests. Please wait a moment and try again."
        if code in ("AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"):
            return f"AWS rejected the request ({code}). Check the AWS credentials this app is running with."
        return f"The agent returned an error ({code}): {err.response['Error']['Message']}"
    if isinstance(err, BotoCoreError):
        return f"Could not reach the agent (timeout or connection problem): {err}"
    return f"Something went wrong while the agent was answering: {err}"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Returns & Refunds Assistant", page_icon=":package:")
st.title("Returns & Refunds Assistant")

if "tokens" not in st.session_state:
    render_login()
    st.stop()

email: str = st.session_state.email
actor_id: str = email.split("@")[0]

with st.sidebar:
    st.header("Session")
    st.write(f"**User:** {email}")
    st.write("**Session ID:**")
    st.code(st.session_state.session_id, language=None)
    if st.button("Log out"):
        sign_out()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Ask about an order, a customer, or a return policy"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            reply: str = st.write_stream(stream_agent_response(prompt, actor_id, st.session_state.session_id))
        except (ClientError, BotoCoreError, RuntimeError) as err:
            reply = friendly_error(err)
            st.error(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
