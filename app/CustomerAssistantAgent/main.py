import os
from typing import Any
from collections import OrderedDict
from strands import Agent, tool
import asyncio
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from model.load import load_model
from mcp_client.client import get_streamable_http_mcp_client

# Strands' built-in tool for returning the current date/time, shipped in the
# separate strands-agents-tools package (imported as strands_tools). Import
# the decorated function itself, not the submodule, for an explicit tool object.
from strands_tools.current_time import current_time

app = BedrockAgentCoreApp()
log = app.logger

# MCP client for the AgentCore Gateway (Cognito OAuth handled in mcp_client/client.py)
mcp_clients = [get_streamable_http_mcp_client()]

DEFAULT_SYSTEM_PROMPT = """
You are the Returns & Refunds Assistant. Introduce yourself as such when greeting a user.

You are used by administrators, not end customers. The person you are talking to is an
administrator who has access to customer data, order records, and return policies, and is
working on behalf of customers.

Your job is to help administrators:
- Check whether an order is eligible for return
- Calculate refund amounts
- Answer questions about return policies

Tools: you can look up orders (order_lookup), customers (user_lookup), products (product_lookup)
and return policies (retrieve_policy). The gateway prefixes these names with the target name,
for example data-lookup___order_lookup. Order records only hold customer_id, product_id, status and
purchase date, so use product_lookup for product details. Return policies differ by country (US, UK,
IN): when a question is about a specific customer, call user_lookup first and pass that customer's
country_code to retrieve_policy; otherwise use the country the administrator names.

If a question has a part you can answer with a tool right away (for example a policy question that
names a country and product type), answer that part first, then ask only for the details still missing
(for example which customer's order the administrator means).

Be helpful and concise. Always confirm the relevant details (order ID, customer, product,
amount) with the administrator before taking any action to process a return or refund.
Use tools when appropriate.
"""

# Admin-facing agent: every session acts on behalf of the same administrator,
# so actor_id defaults to a fixed value rather than being derived per-request.
DEFAULT_ACTOR_ID: str = "administrator"
AWS_REGION: str = os.environ.get("AWS_REGION", "us-west-2")
# AgentCore CLI 1.0 binds each memory to the runtime and injects its ID as
# AGENTCORE_MEMORY_<NAME>_ID (and grants the execution role access to that exact
# memory), so we read it instead of hard-coding an ID that changes if the memory is
# recreated. AGENTCORE_MEMORY_ID is kept as a fallback for local development.
AGENTCORE_MEMORY_ID: str | None = os.environ.get(
    "AGENTCORE_MEMORY_CUSTOMERASSISTANTMEMORY_ID"
) or os.environ.get("AGENTCORE_MEMORY_ID")

# Namespace template for the USER_PREFERENCE strategy configured on
# CustomerAssistantMemory (see agentcore/agentcore.json). This is the raw
# {actorId} placeholder template -- AgentCoreMemorySessionManager substitutes
# it internally using the actor_id from AgentCoreMemoryConfig. Do NOT pre-substitute
# it yourself (e.g. f"/users/{actor_id}/preference/") -- pass the literal template
# string here, and it must exactly match the strategy's namespaceTemplates entry.
PREFERENCE_NAMESPACE_TEMPLATE: str = "/users/{actorId}/preference/"


# Define a collection of tools used by the model
tools = []

_INLINE_FUNCTION_NAMES = set()

# Define a simple function tool
@tool
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two numbers"""
    return a+b
tools.append(add_numbers)

# Use the Strands built-in current_time tool (from strands_tools) instead of
# writing a custom one -- it already returns an ISO-8601 timestamp.
tools.append(current_time)


# Gateway tools (order_lookup, user_lookup, product_lookup, retrieve_policy) are discovered
# at runtime from the AgentCore Gateway over MCP, so no local tool definitions are needed.
for mcp_client in mcp_clients:
    if mcp_client:
        tools.append(mcp_client)


def _make_conversation_manager():
    return NullConversationManager()


def _make_memory_session_manager(session_id: str) -> AgentCoreMemorySessionManager | None:
    """Build a per-session AgentCore Memory session manager for long-term preference recall.

    Returns None when AGENTCORE_MEMORY_ID is not set so the agent still runs
    locally without a deployed memory resource.
    """
    if not AGENTCORE_MEMORY_ID:
        log.warning("AGENTCORE_MEMORY_ID not set; running without long-term memory.")
        return None
    memory_config = AgentCoreMemoryConfig(
        memory_id=AGENTCORE_MEMORY_ID,
        session_id=session_id,
        actor_id=DEFAULT_ACTOR_ID,
        retrieval_config={
            # Low threshold (0.3) for testing/demo purposes -- a production
            # deployment would tune this higher to reduce noisy matches.
            PREFERENCE_NAMESPACE_TEMPLATE: RetrievalConfig(top_k=5, relevance_score=0.3),
        },
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=memory_config,
        region_name=AWS_REGION,
    )

# Reuses one Agent per session_id so each session keeps its own in-process
# conversation history (best-effort; resets on cold start). The cache is bounded
# to 128 sessions with LRU eviction (least-recently-used is dropped and its
# history reset) so a single process serving many sessions cannot leak history
# between them or grow without limit. Each cached Agent also gets its own
# AgentCoreMemorySessionManager (built with that session's session_id and the
# shared actor_id) for durable, cross-session long-term preference recall.
def agent_factory():
    cache = OrderedDict()
    def get_or_create_agent(session_id):
        if session_id in cache:
            cache.move_to_end(session_id)
            return cache[session_id]
        if len(cache) >= 128:
            cache.popitem(last=False)
        cache[session_id] = Agent(
            model=load_model(),
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tools=tools,
            conversation_manager=_make_conversation_manager(),
            session_manager=_make_memory_session_manager(session_id),
            hooks=[
            ],
        )
        return cache[session_id]
    return get_or_create_agent
get_or_create_agent = agent_factory()


def strip_trailing_tool_use(messages: Any) -> list[dict]:
    """Strip toolUse blocks from the tail until the last message has none."""
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")

    messages = list(messages)
    while messages:
        last = messages[-1]
        if not isinstance(last, dict):
            raise ValueError("each message must be an object")
        original_content = last.get("content", [])
        if not isinstance(original_content, list) or not all(isinstance(block, dict) for block in original_content):
            raise ValueError("each message content value must be a list of content blocks")

        content = [block for block in original_content if "toolUse" not in block]
        if len(content) == len(original_content):
            break
        if content:
            messages[-1] = {**last, "content": content}
            break
        messages.pop()

    return messages


def _extract_prompt(payload: dict):
    """Accept validated harness messages, tool results, or a plain prompt string."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "messages" in payload:
        return strip_trailing_tool_use(payload["messages"])
    if "tool_results" in payload:
        tool_results = payload["tool_results"]
        if not isinstance(tool_results, list) or not all(
            isinstance(tool_result, dict) and isinstance(tool_result.get("toolUseId"), str)
            for tool_result in tool_results
        ):
            raise ValueError("tool_results must contain objects with a toolUseId string")
        return [{"role": "user", "content": [{"toolResult": {
            "toolUseId": tr["toolUseId"],
            "status": tr.get("status", "success"),
            "content": tr.get("content", []),
        }} for tr in tool_results]}]
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return prompt


def _has_inline_function_call(messages) -> bool:
    """Return True if messages contains an assistant toolUse for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES or not isinstance(messages, list):
        return False
    for msg in messages:
        if msg.get("role") == "assistant":
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("toolUse", {}).get("name") in _INLINE_FUNCTION_NAMES:
                    return True
    return False


def _is_inline_function_call(event: dict) -> bool:
    """Check if a contentBlockStart event is for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES:
        return False
    cbs = event.get("contentBlockStart", {})
    start = cbs.get("start", {})
    tool_use = start.get("toolUse") if isinstance(start, dict) else None
    return tool_use is not None and tool_use.get("name") in _INLINE_FUNCTION_NAMES



@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking Agent.....")


    session_id = getattr(context, 'session_id', 'default-session')
    agent = get_or_create_agent(session_id)

    prompt = _extract_prompt(payload)


    async for event in agent.stream_async(
        prompt,
    ):
        if not isinstance(event, dict) or "event" not in event:
            continue
        cbs = event["event"].get("contentBlockStart")
        if cbs is not None and not cbs.get("start"):
            continue
        yield event


if __name__ == "__main__":
    app.run()
