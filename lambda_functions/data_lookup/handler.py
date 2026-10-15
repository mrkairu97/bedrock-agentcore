"""AgentCore Gateway Lambda target: order, customer and product lookups from DynamoDB."""

from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

REGION: str = "us-west-2"

# Created outside the handler so the connection is reused across warm invocations.
dynamodb = boto3.resource("dynamodb", region_name=REGION)
orders_table = dynamodb.Table("workshop-orders")  # PK: customer_id, SK: product_id
customers_table = dynamodb.Table("workshop-customers")  # PK: customer_id
products_table = dynamodb.Table("workshop-products")  # PK: product_id

# The gateway prefixes tool names with the target name: "<target_name>___<tool_name>".
TOOL_NAME_DELIMITER: str = "___"


def order_lookup(customer_id: str, product_id: Optional[str] = None) -> dict[str, Any]:
    """Return all orders for a customer, or one order if product_id is given."""
    if product_id:
        item = orders_table.get_item(
            Key={"customer_id": customer_id, "product_id": product_id}
        ).get("Item")
        orders = [item] if item else []
    else:
        # customer_id is the partition key, so a Query reads only this customer's orders.
        orders = orders_table.query(
            KeyConditionExpression=Key("customer_id").eq(customer_id)
        )["Items"]
    return {"customer_id": customer_id, "orders": orders, "count": len(orders)}


def user_lookup(customer_id: str) -> dict[str, Any]:
    """Return the customer profile (name, country_code) for a customer_id."""
    item = customers_table.get_item(Key={"customer_id": customer_id}).get("Item")
    if not item:
        return {"error": f"Customer {customer_id} not found"}
    return item


def product_lookup(product_id: str) -> dict[str, Any]:
    """Return product details (name, category, provider) for a product_id."""
    item = products_table.get_item(Key={"product_id": product_id}).get("Item")
    if not item:
        return {"error": f"Product {product_id} not found"}
    return item


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    # The event holds the tool arguments; the tool name arrives in the client context.
    full_tool_name: str = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = full_tool_name.split(TOOL_NAME_DELIMITER)[-1]  # strip the target prefix

    if tool_name == "order_lookup":
        return order_lookup(event["customer_id"], event.get("product_id"))
    if tool_name == "user_lookup":
        return user_lookup(event["customer_id"])
    if tool_name == "product_lookup":
        return product_lookup(event["product_id"])
    return {"error": f"Unknown tool: {tool_name}"}
