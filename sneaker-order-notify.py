"""
sneaker-order-notify Lambda

Trigger: invoked directly by the sneaker-order-pricing Lambda once an
order is priced. Receives a simple payload:
  { "order_id": "..." }

What it does:
  1. Looks up the order in DynamoDB.
  2. Sends the customer a plain, friendly email with their price
     estimate and a short breakdown.

Environment variables:
  DYNAMODB_TABLE   e.g. "sneaker-orders"
  SENDER_EMAIL     the SES-verified address to send FROM,
                    e.g. "orders@yourdomain.com"

IAM role needs:
  - AWSLambdaBasicExecutionRole
  - dynamodb:GetItem on sneaker-orders
  - ses:SendEmail and ses:SendRawEmail

Note: while your SES account is in sandbox mode, the customer's email
address (the "to" address) must ALSO be individually verified in SES,
or sending will fail. Request SES production access to lift this
restriction for real customers.
"""

import json
import os
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
ses = boto3.client("ses")

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "sneaker-orders")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")

table = dynamodb.Table(TABLE_NAME)


def format_breakdown(breakdown):
    """Turns {'base': 1030, 'rush': 150} into a readable list of lines."""
    if not breakdown:
        return ""
    lines = []
    for key, value in breakdown.items():
        label = "Base price" if key == "base" else key.replace("_", " ").title()
        lines.append(f"  - {label}: ${value}")
    return "\n".join(lines)


def build_email_body(order):
    name = order.get("name", "there")
    pattern = order.get("pattern") or "your custom request"
    price = order.get("price_estimate", "TBD")
    breakdown = format_breakdown(order.get("price_breakdown"))

    body = f"""Hi {name},

Thanks for your order request with Deli Bespoke Creations! Here's your build
summary and price estimate:

Pattern: {pattern}
Size: {order.get('size', 'N/A')}
Inspiration: {order.get('inspiration', '(none provided)')}

Price estimate: ${price}
{breakdown}

This estimate is based on the build details you provided. I'll be in
touch if anything needs clarifying before we get started. Typical
builds take 2-4 weeks from here.

Thanks again for trusting me with this one — excited to build it.

— Deli
"""
    return body


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    order_id = event.get("order_id")
    if not order_id:
        logger.error("No order_id provided in event")
        return {"status": "error", "message": "order_id is required"}

    if not SENDER_EMAIL:
        logger.error("SENDER_EMAIL not set — cannot send")
        return {"status": "error", "message": "SENDER_EMAIL not configured"}

    try:
        order = table.get_item(Key={"order_id": order_id}).get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB read failed: {str(e)}")
        return {"status": "error", "message": "Failed to read order"}

    if not order:
        logger.error(f"No order found with id {order_id}")
        return {"status": "error", "message": "Order not found"}

    customer_email = order.get("email")
    if not customer_email:
        logger.error(f"Order {order_id} has no email on file")
        return {"status": "error", "message": "No customer email on order"}

    body = build_email_body(order)

    try:
        ses.send_email(
            Source=SENDER_EMAIL,
            Destination={"ToAddresses": [customer_email]},
            Message={
                "Subject": {"Data": "Your Deli Bespoke Creations order estimate"},
                "Body": {"Text": {"Data": body}},
            },
        )
    except ClientError as e:
        logger.error(f"SES send failed: {str(e)}")
        return {"status": "error", "message": "Failed to send email"}

    logger.info(f"Notification email sent for order {order_id} to {customer_email}")

    return {"status": "sent", "order_id": order_id, "to": customer_email}
