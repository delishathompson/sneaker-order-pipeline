"""
sneaker-order-pricing Lambda

Trigger: invoked directly by the sneaker-order-categorize Lambda (not by
API Gateway) once an order is categorized. Receives a simple payload:
  { "order_id": "..." }

What it does:
  1. Looks up the order in DynamoDB using the order_id.
  2. Looks up the base price for that order's pattern in the
     sneaker-pricing table.
  3. Adds a surcharge for each complexity flag the categorize Lambda
     detected (extra_colorway, custom_sole, intricate_art, rush) —
     also looked up from sneaker-pricing.
  4. Writes price_estimate (and a price_breakdown for transparency)
     back to the order, and updates status to "priced".
  5. Optionally triggers a customer notification Lambda (Phase 7),
     the same way earlier steps chained together — skipped gracefully
     if that function doesn't exist yet.

IAM role (attach to this Lambda's execution role):
  - AWSLambdaBasicExecutionRole (CloudWatch Logs)
  - dynamodb:GetItem on the sneaker-pricing table
  - dynamodb:GetItem and dynamodb:UpdateItem on the sneaker-orders table
  - lambda:InvokeFunction on the notify Lambda (once it exists)

Environment variables:
  DYNAMODB_TABLE        e.g. "sneaker-orders"
  PRICING_TABLE_NAME    e.g. "sneaker-pricing"
  NOTIFY_FUNCTION_NAME  optional — name of the Phase 7 Lambda that
                         emails the customer their estimate. Leave
                         unset until that function exists.
"""

import json
import os
import logging
import boto3
from decimal import Decimal
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

ORDERS_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "sneaker-orders")
PRICING_TABLE_NAME = os.environ.get("PRICING_TABLE_NAME", "sneaker-pricing")
NOTIFY_FUNCTION_NAME = os.environ.get("NOTIFY_FUNCTION_NAME", "")

orders_table = dynamodb.Table(ORDERS_TABLE_NAME)
pricing_table = dynamodb.Table(PRICING_TABLE_NAME)


def get_price(shoe_type_key):
    """Looks up a base_price value from the pricing table by its key
    (either a real pattern name, or an add-on key like 'rush')."""
    try:
        item = pricing_table.get_item(Key={"shoe_type": shoe_type_key}).get("Item")
    except ClientError as e:
        logger.error(f"Pricing table read failed for '{shoe_type_key}': {str(e)}")
        return Decimal("0")

    if not item:
        logger.warning(f"No pricing row found for '{shoe_type_key}' — treating as $0")
        return Decimal("0")

    return item.get("base_price", Decimal("0"))


def calculate_price(order):
    pattern = order.get("pattern")
    complexity_flags = order.get("complexity_flags", []) or []

    breakdown = {}

    if pattern:
        base = get_price(pattern)
    else:
        # No listed pattern — this was a custom_request order. Fall back
        # to a generic base price row you should add to sneaker-pricing
        # named "custom_request_base", or adjust this manually per order.
        base = get_price("custom_request_base")

    breakdown["base"] = base
    total = base

    for flag in complexity_flags:
        addon_price = get_price(flag)
        breakdown[flag] = addon_price
        total += addon_price

    return total, breakdown


def trigger_notify(order_id):
    if not NOTIFY_FUNCTION_NAME:
        logger.info("NOTIFY_FUNCTION_NAME not set — skipping customer notification")
        return

    try:
        lambda_client.invoke(
            FunctionName=NOTIFY_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"order_id": order_id}),
        )
    except ClientError as e:
        logger.error(f"Failed to invoke notify Lambda: {str(e)}")


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    order_id = event.get("order_id")
    if not order_id:
        logger.error("No order_id provided in event")
        return {"status": "error", "message": "order_id is required"}

    try:
        order = orders_table.get_item(Key={"order_id": order_id}).get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB read failed: {str(e)}")
        return {"status": "error", "message": "Failed to read order"}

    if not order:
        logger.error(f"No order found with id {order_id}")
        return {"status": "error", "message": "Order not found"}

    price_estimate, breakdown = calculate_price(order)

    try:
        orders_table.update_item(
            Key={"order_id": order_id},
            UpdateExpression="SET price_estimate = :price, price_breakdown = :breakdown, #s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":price": price_estimate,
                ":breakdown": breakdown,
                ":status": "priced",
            },
        )
    except ClientError as e:
        logger.error(f"DynamoDB update failed: {str(e)}")
        return {"status": "error", "message": "Failed to update order"}

    logger.info(f"Order {order_id} priced at {price_estimate} — breakdown: {breakdown}")

    trigger_notify(order_id)

    return {
        "status": "priced",
        "order_id": order_id,
        "price_estimate": str(price_estimate),
        "price_breakdown": {k: str(v) for k, v in breakdown.items()},
    }