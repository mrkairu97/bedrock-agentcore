"""AgentCore Gateway Lambda target: retrieve return policies from a Bedrock Knowledge Base."""

from typing import Any, Optional

import boto3

REGION: str = "us-west-2"
KB_ID_PARAMETER: str = "/app/workshop/kb/knowledge-base-id"

ssm = boto3.client("ssm", region_name=REGION)
# Knowledge base queries use the bedrock-agent-runtime client (not bedrock-agent).
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)

_knowledge_base_id: Optional[str] = None  # cached across warm invocations


def get_knowledge_base_id() -> str:
    """Read the Knowledge Base ID from SSM once, then reuse it."""
    global _knowledge_base_id
    if _knowledge_base_id is None:
        _knowledge_base_id = ssm.get_parameter(Name=KB_ID_PARAMETER)["Parameter"]["Value"]
    return _knowledge_base_id


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    # The event holds the tool arguments defined in the target's inputSchema.
    query: str = event["query"]
    country: Optional[str] = event.get("country_code")  # e.g. "US", "UK", "IN"

    vector_config: dict[str, Any] = {"numberOfResults": event.get("number_of_results", 5)}
    if country:
        # Each policy PDF is tagged with a "country" metadata attribute, so filtering
        # keeps results from other countries' policies out.
        vector_config["filter"] = {"equals": {"key": "country", "value": country.upper()}}

    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=get_knowledge_base_id(),
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": vector_config},
    )

    results = [
        {
            "text": r["content"]["text"],
            "score": r.get("score"),
            "source": r.get("location", {}).get("s3Location", {}).get("uri"),
        }
        for r in response["retrievalResults"]
    ]
    return {"query": query, "country_code": country, "results": results}
