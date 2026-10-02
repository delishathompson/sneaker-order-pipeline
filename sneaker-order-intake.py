"""
sneaker-order-intake Lambda

Trigger: API Gateway (Lambda proxy integration) on POST /orders.
The order form's fetch() call hits this endpoint directly with the
JSON payload described below.

What it does:
  1. Validates the incoming payload from the order form.
  2. Generates an order_id and a server-side timestamp.
  3. Writes the order to DynamoDB with status "pending_approval".
  4. Publishes a summary to SNS so you get notified (email/SMS) to
     review and approve/reject it.

IAM role (attach to this Lambda's execution role):
  - AWSLambdaBasicExecutionRole (CloudWatch Logs)
  - dynamodb:PutItem on the sneaker-orders table only
  - sns:Publish on the new-sneaker-orders topic only
  (Scope each to the specific resource ARN, not "*" — same
  least-privilege pattern as the ServiceNow project.)

Environment variables:
  DYNAMODB_TABLE   e.g. "sneaker-orders"
  SNS_TOPIC_ARN    ARN of the "new-sneaker-orders" SNS topic
  APPROVAL_BASE_URL  optional — base URL for approve/reject links,
                      e.g. "https://YOUR-API-ID.execute-api.REGION.amazonaws.com"

Expected payload from the order form:
  {
    "pattern": "Canvas hi-top" | null,
    "size": "9" | null,
    "inspiration": "free text, can be empty string",
    "custom_request": "free text" | null,
    "name": "required, non-empty",
    "email": "required, non-empty",
    "submitted_at": "ISO 8601 string from the browser"
  }
"""

import json
import os
import uuid
import logging
import boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "sneaker-orders")
TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
APPROVAL_BASE_URL = os.environ.get("APPROVAL_BASE_URL", "")

table = dynamodb.Table(TABLE_NAME)


def _response(status_code, body_dict):
    """Standard API Gateway proxy response, with CORS headers for the form."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body_dict),
    }


def validate_payload(payload):
    """Returns a list of error strings; empty list means valid."""
    errors = []

    if not payload.get("name", "").strip():
        errors.append("name is required")
    if not payload.get("email", "").strip():
        errors.append("email is required")

    pattern = payload.get("pattern")
    size = payload.get("size")
    custom_request = payload.get("custom_request")

    has_listed_selection = bool(pattern) and bool(size)
    has_custom_request = bool(custom_request and custom_request.strip())

    if not has_listed_selection and not has_custom_request:
        errors.append("either pattern+size or custom_request must be provided")

    return errors


def build_order_item(payload):
    order_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    custom_request = payload.get("custom_request")
    needs_manual_review = bool(custom_request and custom_request.strip())

    return {
        "order_id": order_id,
        "status": "pending_approval",
        "needs_manual_review": needs_manual_review,
        "pattern": payload.get("pattern"),
        "size": payload.get("size"),
        "inspiration": payload.get("inspiration", ""),
        "custom_request": custom_request,
        "name": payload.get("name", "").strip(),
        "email": payload.get("email", "").strip(),
        "submitted_at_client": payload.get("submitted_at"),
        "created_at": now,
    }


def notify_for_approval(order_item):
    if not TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set — skipping notification")
        return

    order_id = order_item["order_id"]
    review_flag = " (NEEDS REVIEW — custom request)" if order_item["needs_manual_review"] else ""

    lines = [
        f"New order{review_flag}",
        f"Order ID: {order_id}",
        f"From: {order_item['name']} <{order_item['email']}>",
        f"Pattern: {order_item['pattern'] or '(not listed)'}",
        f"Size: {order_item['size'] or '(not listed)'}",
        f"Inspiration: {order_item['inspiration'] or '(none provided)'}",
    ]
    if order_item["custom_request"]:
        lines.append(f"Custom request: {order_item['custom_request']}")

    if APPROVAL_BASE_URL:
        lines.append("")
        lines.append(f"Approve: {APPROVAL_BASE_URL}/orders/{order_id}/approve")
        lines.append(f"Reject:  {APPROVAL_BASE_URL}/orders/{order_id}/reject")

    message = "\n".join(lines)

    sns.publish(
        TopicArn=TOPIC_ARN,
        Subject=f"New sneaker order — {order_item['name']}",
        Message=message,
    )


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    errors = validate_payload(payload)
    if errors:
        return _response(400, {"error": "Validation failed", "details": errors})

    order_item = build_order_item(payload)

    try:
        table.put_item(Item=order_item)
    except Exception as e:
        logger.error(f"DynamoDB write failed: {str(e)}")
        return _response(500, {"error": "Failed to save order"})

    try:
        notify_for_approval(order_item)
    except Exception as e:
        logger.error(f"SNS publish failed: {str(e)}")

    return _response(201, {
        "order_id": order_item["order_id"],
        "status": order_item["status"],
    })