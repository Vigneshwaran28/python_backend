from flask import Blueprint, request
from db.connection import fetch_one, fetch_all
from middleware.rate_limit import public_limiter
from middleware.auth import parse_features
from utils.helpers import HttpError, json_response, safe_json_loads

public_bp = Blueprint("public", __name__, url_prefix="/api/public")

@public_bp.route("/status", methods=["GET"])
@public_limiter
def get_public_status():
    resp = json_response({
        "ok": True,
        "maintenance": {
            "active": None,
            "upcoming": None
        }
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp

@public_bp.route("/workspaces/<slug>", methods=["GET"])
@public_limiter
def get_public_workspace(slug):
    slug_val = (slug or "").strip().lower()
    tenant = fetch_one("SELECT * FROM tenants WHERE LOWER(slug) = LOWER(%s) AND deleted_at IS NULL LIMIT 1;", (slug_val,))
    if not tenant:
        raise HttpError(404, "We couldn't find that workspace. Check the address with your administrator.", code="NOT_FOUND")

    settings = safe_json_loads(tenant.get("settings"))
    branding = settings.get("branding", {})
    features = parse_features(tenant.get("features"))

    return json_response({
        "slug": tenant["slug"],
        "name": tenant["name"],
        "active": tenant.get("status") == "active",
        "siteTitle": branding.get("siteTitle") or tenant["name"],
        "tagline": branding.get("tagline"),
        "accent": branding.get("accent", "indigo"),
        "logoIcon": branding.get("logoIcon"),
        "logoUrl": branding.get("logoUrl"),
        "login": branding.get("login", {}),
        "features": features,
        "maintenance": {"active": None, "upcoming": None}
    })

@public_bp.route("/track/<code>", methods=["GET"])
@public_limiter
def get_public_tracking(code):
    code_val = (code or "").strip().upper()
    query = """
        SELECT s.*, t.name as tenant_name, t.settings as tenant_settings, c.name as carrier_name
        FROM shipments s
        JOIN tenants t ON t.id = s.tenant_id
        LEFT JOIN carriers c ON c.id = s.carrier_id
        WHERE UPPER(s.tracking_code) = %s AND t.deleted_at IS NULL
        LIMIT 1;
    """
    row = fetch_one(query, (code_val,))
    if not row:
        raise HttpError(404, "We couldn't find a shipment with that tracking code.", code="NOT_FOUND")

    events = fetch_all("""
        SELECT * FROM shipment_events
        WHERE shipment_id = %s
        ORDER BY occurred_at DESC;
    """, (row["id"],))

    return json_response({
        "trackingCode": row["tracking_code"],
        "number": row.get("number"),
        "status": row.get("status"),
        "service": row.get("service"),
        "originCity": row.get("origin_city"),
        "destinationCity": row.get("destination_city"),
        "expectedOn": row.get("expected_on"),
        "deliveredAt": row.get("delivered_at"),
        "packages": int(row.get("packages") or 1),
        "carrier": {"name": row.get("carrier_name")} if row.get("carrier_name") else None,
        "events": [
            {
                "status": e.get("status"),
                "location": e.get("location"),
                "note": e.get("note"),
                "occurredAt": e.get("occurred_at")
            }
            for e in events
        ],
        "company": row.get("tenant_name")
    })
