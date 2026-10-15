"""Zip and deploy the two gateway Lambda functions to us-west-2 with boto3."""

import io
import zipfile
from pathlib import Path

import boto3

REGION: str = "us-west-2"
ROLE_PARAMETER: str = "/app/workshop/lambda/execution-role-arn"
BASE_DIR: Path = Path(__file__).parent

# function name -> source folder (each holds handler.py)
FUNCTIONS: dict[str, str] = {
    "workshop-data-lookup": "data_lookup",
    "workshop-policy-retrieval": "policy_retrieval",
}

ssm = boto3.client("ssm", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)


def zip_handler(folder: str) -> bytes:
    """Build an in-memory zip with handler.py at the root, where Lambda expects it.

    boto3 is already in the Lambda Python runtime, so requirements.txt is not bundled.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(BASE_DIR / folder / "handler.py", arcname="handler.py")
    return buffer.getvalue()


def deploy(name: str, folder: str, role_arn: str) -> str:
    """Create the function, or update its code if it already exists. Returns the ARN."""
    code = zip_handler(folder)
    try:
        response = lambda_client.create_function(
            FunctionName=name,
            Runtime="python3.13",
            Role=role_arn,  # pre-created execution role from SSM
            Handler="handler.lambda_handler",  # <file>.<function>
            Code={"ZipFile": code},
            Timeout=30,
            MemorySize=256,
        )
    except lambda_client.exceptions.ResourceConflictException:
        response = lambda_client.update_function_code(FunctionName=name, ZipFile=code)
    # Wait until the function is Active/updated before reporting success.
    lambda_client.get_waiter("function_active_v2").wait(FunctionName=name)
    return response["FunctionArn"]


if __name__ == "__main__":
    role = ssm.get_parameter(Name=ROLE_PARAMETER)["Parameter"]["Value"]
    for function_name, source_folder in FUNCTIONS.items():
        print(f"{function_name}: {deploy(function_name, source_folder, role)}")
