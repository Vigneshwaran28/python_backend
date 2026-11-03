from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

sales_bp = Blueprint("sales", __name__, url_prefix="/api/sales")

# ==========================================
# DEALS & PIPELINE
# ==========================================

@sales_bp.route("/deals", methods=["GET"])
@require_auth
@require_feature("sales")
@require_permission("sales.view")
def get_deals():
    tenant_id = g.user["tenant_id"]
    query = """
        SELECT d.*, c.name as customer_name, u.name as owner_name
        FROM deals d
        LEFT JOIN customers c ON c.id = d.customer_id
        LEFT JOIN users u ON u.id = d.owner_id
        WHERE d.tenant_id = %s
        ORDER BY d.id DESC;
    """
    rows = fetch_all(query, (tenant_id,))
    return json_response([
        {
            "id": r["id"],
            "title": r["title"],
            "value": float(r.get("value") or 0),
            "stage": r.get("stage", "lead"),
            "customerId": r.get("customer_id"),
            "customerName": r.get("customer_name"),
            "ownerId": r.get("owner_id"),
            "ownerName": r.get("owner_name"),
            "expectedClose": r.get("expected_close"),
            "createdAt": r.get("created_at")
        }
        for r in rows
    ])

@sales_bp.route("/deals", methods=["POST"])
@require_auth
@require_feature("sales")
@require_permission("sales.edit")
def create_deal():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"title": "Title is required."})

    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    now = now_iso()

    deal_id = insert_and_get_id("deals", {
        "tenant_id": tenant_id,
        "customer_id": parse_id(data.get("customerId")) if data.get("customerId") else None,
        "owner_id": parse_id(data.get("ownerId")) if data.get("ownerId") else user_id,
        "title": title[:200],
        "value": float(data.get("value", 0)),
        "stage": data.get("stage", "lead"),
        "expected_close": data.get("expectedClose"),
        "notes": data.get("notes"),
        "created_at": now,
        "updated_at": now
    })

    return json_response({"id": deal_id, "title": title, "stage": data.get("stage", "lead")}, status=201)

# ==========================================
# CUSTOMERS
# ==========================================

@sales_bp.route("/customers", methods=["GET"])
@require_auth
@require_feature("sales")
@require_permission("sales.view")
def get_customers():
    tenant_id = g.user["tenant_id"]
    rows = fetch_all("SELECT * FROM customers WHERE tenant_id = %s ORDER BY name ASC;", (tenant_id,))
    return json_response([
        {
            "id": c["id"],
            "name": c["name"],
            "company": c.get("company"),
            "email": c.get("email"),
            "phone": c.get("phone"),
            "address": c.get("address"),
            "createdAt": c.get("created_at")
        }
        for c in rows
    ])

@sales_bp.route("/customers", methods=["POST"])
@require_auth
@require_feature("sales")
@require_permission("sales.edit")
def create_customer():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Name is required."})

    tenant_id = g.user["tenant_id"]
    now = now_iso()

    cid = insert_and_get_id("customers", {
        "tenant_id": tenant_id,
        "name": name[:160],
        "company": data.get("company"),
        "email": data.get("email"),
        "phone": data.get("phone"),
        "address": data.get("address"),
        "notes": data.get("notes"),
        "created_at": now,
        "updated_at": now
    })

    return json_response({"id": cid, "name": name, "email": data.get("email")}, status=201)
