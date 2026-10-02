"""
sneaker-order-categorize Lambda

Trigger: invoked directly by the sneaker-order-approve Lambda (not by
API Gateway) once an order is approved. Receives a simple payload:
  { "order_id": "..." }

What it does:
  1. Looks up the order in DynamoDB using the order_id.
  2. Determines a design_category based on the order's pattern and
     inspiration/custom_request text (rule-based keyword matching).
  3. Flags any complexity add-ons detected in the order text (extra
     colorway, custom sole, intricate art, rush) — these feed directly
     into the Phase 6 pricing Lambda.
  4. Writes design_category and complexity_flags back to the order,
     and triggers the pricing Lambda the same way approve triggered
     this one.

IAM role (attach to this Lambda's execution role):
  - AWSLambdaBasicExecutionRole (CloudWatch Logs)
  - dynamodb:GetItem and dynamodb:UpdateItem on the sneaker-orders table
  - lambda:InvokeFunction on the pricing Lambda (once it exists)

Environment variables:
  DYNAMODB_TABLE       e.g. "sneaker-orders"
  PRICING_FUNCTION_NAME  optional — name of the Phase 6 Lambda,
                          e.g. "sneaker-order-pricing". Leave unset
                          until that function exists.
"""

import json
import os
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "sneaker-orders")
PRICING_FUNCTION_NAME = os.environ.get("PRICING_FUNCTION_NAME", "")

table = dynamodb.Table(TABLE_NAME)

# ---------------------------------------------------------------
# EDIT THIS to match your real pattern names from the order form's
# PATTERNS list, and adjust the category keywords as you see fit.
# ---------------------------------------------------------------
PATTERN_CATEGORY_MAP = {
    "air force 1 base": "streetwear",
    "canvas hi-top": "streetwear",
    "low-top slip-on": "streetwear",
    "chelsea boot": "other",
    "custom cleats": "sports tribute",
}

# Keywords checked against inspiration/custom_request text, in case the
# pattern alone doesn't tell you the design category.
CATEGORY_KEYWORDS = {
    "sports tribute": ["team", "jersey", "varsity", "league", "championship", "tribute"],
    "licensed-style": ["character", "movie", "show", "cartoon", "brand logo"],
}

# Keywords that flag a complexity add-on for pricing purposes.
COMPLEXITY_KEYWORDS = {
    "extra_colorway": ["multiple colors", "multi-color", "several colors", "colorway"],
    "custom_sole": ["custom sole", "sole design", "painted sole"],
    "intricate_art": ["photorealistic", "portrait", "detailed art", "intricate"],
    "rush": ["rush", "asap", "urgent", "need it by", "deadline"],
}


def determine_category(order):
    pattern = (order.get("pattern") or "").strip().lower()
    text = f"{order.get('inspiration', '')} {order.get('custom_request', '') or ''}".lower()

    # Keyword match takes priority over the pattern's default mapping,
    # since a "tribute" AF1 build should count as sports tribute, not
    # just streetwear by default.
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category

    return PATTERN_CATEGORY_MAP.get(pattern, "other")


def determine_complexity_flags(order):
    text = f"{order.get('inspiration', '')} {order.get('custom_request', '') or ''}".lower()
    flags = []
    for flag_name, keywords in COMPLEXITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            flags.append(flag_name)
    return flags


def trigger_pricing(order_id):
    if not PRICING_FUNCTION_NAME:
        logger.info("PRICING_FUNCTION_NAME not set — skipping pricing trigger")
        return

    try:
        lambda_client.invoke(
            FunctionName=PRICING_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"order_id": order_id}),
        )
    except ClientError as e:
        logger.error(f"Failed to invoke pricing Lambda: {str(e)}")


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    order_id = event.get("order_id")
    if not order_id:
        logger.error("No order_id provided in event")
        return {"status": "error", "message": "order_id is required"}

    try:
        order = table.get_item(Key={"order_id": order_id}).get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB read failed: {str(e)}")
        return {"status": "error", "message": "Failed to read order"}

    if not order:
        logger.error(f"No order found with id {order_id}")
        return {"status": "error", "message": "Order not found"}

    design_category = determine_category(order)
    complexity_flags = determine_complexity_flags(order)

    try:
        table.update_item(
            Key={"order_id": order_id},
            UpdateExpression="SET design_category = :cat, complexity_flags = :flags, #s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":cat": design_category,
                ":flags": complexity_flags,
                ":status": "categorized",
            },
        )
    except ClientError as e:
        logger.error(f"DynamoDB update failed: {str(e)}")
        return {"status": "error", "message": "Failed to update order"}

    logger.info(f"Order {order_id} categorized as {design_category} with flags {complexity_flags}")

    trigger_pricing(order_id)

    return {
        "status": "categorized",
        "order_id": order_id,
        "design_category": design_category,
        "complexity_flags": complexity_flags,
    }