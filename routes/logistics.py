import random
from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

logistics_bp = Blueprint("logistics", __name__, url_prefix="/api/logistics")

def format_shipment(row: dict, events: list = None) -> dict:
    return {
        "id": row["id"],
        "trackingCode": row.get("tracking_code"),
        "tracking_number": row.get("tracking_code"),
        "number": row.get("number"),
        "status": row.get("status", "pending"),
        "service": row.get("service", "standard"),
        "originCity": row.get("origin_city"),
        "destinationCity": row.get("destination_city"),
        "recipientName": row.get("recipient_name"),
        "recipientAddress": row.get("recipient_address"),
        "recipientPhone": row.get("recipient_phone"),
        "carrierId": row.get("carrier_id"),
        "carrier": {"id": row.get("carrier_id"), "name": row.get("carrier_name")} if row.get("carrier_name") else None,
        "packages": int(row.get("packages") or 1),
        "expectedOn": row.get("expected_on"),
        "dispatchedAt": row.get("dispatched_at"),
        "deliveredAt": row.get("delivered_at"),
        "events": events or [],
        "createdAt": row.get("created_at")
    }

@logistics_bp.route("/shipments", methods=["GET"])
@require_auth
@require_feature("logistics")
@require_permission("logistics.view")
def get_shipments():
    tenant_id = g.user["tenant_id"]
    query = """
        SELECT s.*, c.name as carrier_name
        FROM shipments s
        LEFT JOIN carriers c ON c.id = s.carrier_id
        WHERE s.tenant_id = %s
        ORDER BY s.id DESC;
    """
    rows = fetch_all(query, (tenant_id,))
    return json_response([format_shipment(r) for r in rows])

@logistics_bp.route("/shipments", methods=["POST"])
@require_auth
@require_feature("logistics")
@require_permission("logistics.edit")
def create_shipment():
    data = request.get_json(silent=True) or {}
    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    now = now_iso()

    code = f"TF-TRK-{random.randint(100000, 999999)}"

    sid = insert_and_get_id("shipments", {
        "tenant_id": tenant_id,
        "created_by": user_id,
        "tracking_code": code,
        "number": f"SHP-{random.randint(1000, 9999)}",
        "status": "pending",
        "service": data.get("service", "standard"),
        "origin_city": data.get("originCity"),
        "destination_city": data.get("destinationCity"),
        "recipient_name": data.get("recipientName"),
        "recipient_address": data.get("recipientAddress"),
        "recipient_phone": data.get("recipientPhone"),
        "packages": int(data.get("packages", 1)),
        "carrier_id": parse_id(data.get("carrierId")) if data.get("carrierId") else None,
        "expected_on": data.get("expectedOn"),
        "created_at": now,
        "updated_at": now
    })

    # Log initial event
    insert_and_get_id("shipment_events", {
        "shipment_id": sid,
        "status": "pending",
        "location": data.get("originCity"),
        "note": "Shipment label created",
        "occurred_at": now
    })

    row = fetch_one("""
        SELECT s.*, c.name as carrier_name
        FROM shipments s
        LEFT JOIN carriers c ON c.id = s.carrier_id
        WHERE s.id = %s;
    """, (sid,))
    return json_response(format_shipment(row), status=201)

@logistics_bp.route("/shipments/<sid>", methods=["GET"])
@require_auth
@require_feature("logistics")
@require_permission("logistics.view")
def get_shipment(sid):
    ship_id = parse_id(sid)
    tenant_id = g.user["tenant_id"]

    row = fetch_one("""
        SELECT s.*, c.name as carrier_name
        FROM shipments s
        LEFT JOIN carriers c ON c.id = s.carrier_id
        WHERE s.id = %s AND s.tenant_id = %s;
    """, (ship_id, tenant_id))

    if not row:
        raise HttpError(404, "Shipment not found.")

    events = fetch_all("SELECT * FROM shipment_events WHERE shipment_id = %s ORDER BY occurred_at DESC;", (ship_id,))
    return json_response(format_shipment(row, events=events))

@logistics_bp.route("/shipments/<sid>", methods=["PATCH"])
@require_auth
@require_feature("logistics")
@require_permission("logistics.edit", "logistics.dispatch", "logistics.deliver")
def update_shipment(sid):
    ship_id = parse_id(sid)
    tenant_id = g.user["tenant_id"]

    existing = fetch_one("SELECT * FROM shipments WHERE id = %s AND tenant_id = %s;", (ship_id, tenant_id))
    if not existing:
        raise HttpError(404, "Shipment not found.")

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": now_iso()}

    if "status" in data:
        st = data["status"]
        updates["status"] = st
        if st == "dispatched":
            updates["dispatched_at"] = now_iso()
        elif st == "delivered":
            updates["delivered_at"] = now_iso()

    set_clauses = [f'"{k}" = %s' for k in updates.keys()]
    values = list(updates.values()) + [ship_id, tenant_id]
    execute(f'UPDATE shipments SET {", ".join(set_clauses)} WHERE id = %s AND tenant_id = %s;', tuple(values))

    # Add event
    if "status" in data:
        insert_and_get_id("shipment_events", {
            "shipment_id": ship_id,
            "status": data["status"],
            "location": data.get("location"),
            "note": data.get("note") or f"Status changed to {data['status']}",
            "occurred_at": now_iso()
        })

    return get_shipment(sid)
