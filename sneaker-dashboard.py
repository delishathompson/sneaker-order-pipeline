"""
sneaker-dashboard Lambda

Handles two routes, kept separate from your intake and approve
functions:
  GET  /dashboard/orders        -> returns all orders, newest first
  POST /orders/{id}/advance     -> moves an order to its next production
                                    stage (priced -> cutting -> painting
                                    -> sealing -> shipped)

This does NOT touch or replace sneaker-order-intake or
sneaker-order-approve — it's a third, independent function.

Environment variables:
  DYNAMODB_TABLE   e.g. "sneaker-orders"

IAM role needs:
  - AWSLambdaBasicExecutionRole
  - dynamodb:Scan and dynamodb:UpdateItem on sneaker-orders
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

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "sneaker-orders")
table = dynamodb.Table(TABLE_NAME)

PRODUCTION_STAGES = ["priced", "cutting", "painting", "sealing", "shipped"]


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o) if o % 1 else int(o)
        return super().default(o)


def _json_response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body_dict, cls=DecimalEncoder),
    }


def get_route_info(event):
    method = event.get("requestContext", {}).get("http", {}).get("method")
    path = event.get("rawPath", "")
    if not method:
        method = event.get("httpMethod", "")
        path = event.get("path", "")
    return method, path


def handle_list_orders():
    try:
        response = table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))
    except ClientError as e:
        logger.error(f"DynamoDB scan failed: {str(e)}")
        return _json_response(500, {"error": "Failed to load orders"})

    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return _json_response(200, {"orders": items})


def handle_advance_stage(event):
    order_id = (event.get("pathParameters") or {}).get("id")
    if not order_id:
        return _json_response(400, {"error": "Missing order id"})

    try:
        existing = table.get_item(Key={"order_id": order_id}).get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB read failed: {str(e)}")
        return _json_response(500, {"error": "Failed to read order"})

    if not existing:
        return _json_response(404, {"error": "Order not found"})

    current_status = existing.get("status")
    if current_status not in PRODUCTION_STAGES:
        return _json_response(
            400,
            {"error": f"Order is at status '{current_status}', which isn't a production stage that can be advanced."},
        )

    current_index = PRODUCTION_STAGES.index(current_status)
    if current_index == len(PRODUCTION_STAGES) - 1:
        return _json_response(400, {"error": "Order is already at the final stage (shipped)."})

    next_status = PRODUCTION_STAGES[current_index + 1]

    try:
        table.update_item(
            Key={"order_id": order_id},
            UpdateExpression="SET #s = :new_status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":new_status": next_status},
        )
    except ClientError as e:
        logger.error(f"DynamoDB update failed: {str(e)}")
        return _json_response(500, {"error": "Failed to update order"})

    return _json_response(200, {"order_id": order_id, "status": next_status})


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    method, path = get_route_info(event)
    logger.info(f"Dispatching method={method} path={path}")

    if method == "GET" and path == "/dashboard/orders":
        return handle_list_orders()

    if method == "POST" and path.endswith("/advance"):
        return handle_advance_stage(event)

    return _json_response(404, {"error": "No matching route for this request"})