"""
sneaker-order-approve Lambda

Trigger: API Gateway (Lambda proxy integration) on two routes:
  GET /orders/{id}/approve
  GET /orders/{id}/reject

These are the links your SNS notification email contains. Clicking one
hits this Lambda directly in the browser.

What it does:
  1. Reads the order_id from the URL path.
  2. Reads whether this was the "approve" or "reject" route.
  3. Updates that order's status in DynamoDB.
  4. If approved, optionally kicks off the categorization Lambda (once
     you've built it in Phase 5) by invoking it directly — this is
     wired to skip gracefully if that function doesn't exist yet, so
     approving orders won't break before Phase 5 is done.
  5. Returns a simple HTML page confirming what happened, since a real
     person is looking at this in a browser, not code parsing JSON.

IAM role (attach to this Lambda's execution role):
  - AWSLambdaBasicExecutionRole (CloudWatch Logs)
  - dynamodb:GetItem and dynamodb:UpdateItem on the sneaker-orders table
  - lambda:InvokeFunction on the categorization Lambda (once it exists —
    safe to add AWSLambdaRole or scope it down later)

Environment variables:
  DYNAMODB_TABLE          e.g. "sneaker-orders"
  CATEGORIZE_FUNCTION_NAME  optional — name of the Phase 5 Lambda,
                            e.g. "sneaker-order-categorize". Leave unset
                            until that function exists.
"""

import json
import os
import logging
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "sneaker-orders")
CATEGORIZE_FUNCTION_NAME = os.environ.get("CATEGORIZE_FUNCTION_NAME", "")

table = dynamodb.Table(TABLE_NAME)


def _html_response(status_code, title, message):
    """A plain, readable confirmation page for a human clicking an email link."""
    body = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>{title}</title>
<style>
  body {{ font-family: sans-serif; max-width: 480px; margin: 80px auto; padding: 0 20px; color: #211F1A; }}
  h1 {{ font-size: 20px; }}
  p {{ color: #5B564A; }}
</style>
</head>
<body>
  <h1>{title}</h1>
  <p>{message}</p>
</body>
</html>"""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html"},
        "body": body,
    }


def determine_action(event):
    """Reads the request path to figure out approve vs reject."""
    path = event.get("rawPath", "") or event.get("requestContext", {}).get("http", {}).get("path", "")
    if path.endswith("/approve"):
        return "approve"
    if path.endswith("/reject"):
        return "reject"
    return None


def trigger_categorization(order_id):
    """Best-effort call to the Phase 5 categorization Lambda. Safe no-op if not set up yet."""
    if not CATEGORIZE_FUNCTION_NAME:
        logger.info("CATEGORIZE_FUNCTION_NAME not set — skipping categorization trigger")
        return

    try:
        lambda_client.invoke(
            FunctionName=CATEGORIZE_FUNCTION_NAME,
            InvocationType="Event",  # async — don't make the approver wait
            Payload=json.dumps({"order_id": order_id}),
        )
    except ClientError as e:
        logger.error(f"Failed to invoke categorization Lambda: {str(e)}")


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    order_id = (event.get("pathParameters") or {}).get("id")
    action = determine_action(event)

    if not order_id or action not in ("approve", "reject"):
        return _html_response(400, "Invalid request", "This link is missing required information.")

    # Confirm the order actually exists first.
    try:
        existing = table.get_item(Key={"order_id": order_id}).get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB read failed: {str(e)}")
        return _html_response(500, "Something went wrong", "Couldn't look up this order. Please try again.")

    if not existing:
        return _html_response(404, "Order not found", f"No order found with ID {order_id}.")

    if existing.get("status") != "pending_approval":
        return _html_response(
            200,
            "Already handled",
            f"This order is already marked as \"{existing.get('status')}\" — no changes made.",
        )

    new_status = "approved" if action == "approve" else "rejected"
    now = datetime.now(timezone.utc).isoformat()

    try:
        table.update_item(
            Key={"order_id": order_id},
            UpdateExpression="SET #s = :new_status, decided_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":new_status": new_status, ":now": now},
        )
    except ClientError as e:
        logger.error(f"DynamoDB update failed: {str(e)}")
        return _html_response(500, "Something went wrong", "Couldn't update this order. Please try again.")

    if new_status == "approved":
        trigger_categorization(order_id)

    customer_name = existing.get("name", "the customer")
    if new_status == "approved":
        message = f"Order from {customer_name} has been approved and moved to categorization."
    else:
        message = f"Order from {customer_name} has been rejected."

    return _html_response(200, f"Order {new_status}", message)