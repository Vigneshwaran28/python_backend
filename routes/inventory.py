from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

inventory_bp = Blueprint("inventory", __name__, url_prefix="/api/inventory")

def format_product(row: dict) -> dict:
    qty = float(row.get("quantity") or 0)
    cost = float(row.get("cost_price") or 0)
    reorder = float(row.get("reorder_level") or 0)
    return {
        "id": row["id"],
        "sku": row["sku"],
        "name": row["name"],
        "category": row.get("category"),
        "unit": row.get("unit", "pcs"),
        "costPrice": cost,
        "salePrice": float(row.get("sale_price") or 0),
        "quantity": qty,
        "reorderLevel": reorder,
        "stockValue": round(qty * cost, 2),
        "isLow": qty <= reorder,
        "description": row.get("description"),
        "isActive": bool(row.get("is_active", True)),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at")
    }

@inventory_bp.route("/products", methods=["GET"])
@require_auth
@require_feature("inventory")
@require_permission("inventory.view")
def get_products():
    tenant_id = g.user["tenant_id"]
    search = request.args.get("search")
    query = "SELECT * FROM products WHERE tenant_id = %s"
    params = [tenant_id]

    if search:
        query += " AND (LOWER(name) LIKE %s OR LOWER(sku) LIKE %s)"
        term = f"%{search.lower()}%"
        params.extend([term, term])

    query += " ORDER BY name ASC;"
    rows = fetch_all(query, tuple(params))
    return json_response([format_product(r) for r in rows])

@inventory_bp.route("/products", methods=["POST"])
@require_auth
@require_feature("inventory")
@require_permission("inventory.products.edit")
def create_product():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    sku = (data.get("sku") or "").strip().upper()

    if not name or not sku:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={
            "name": "Name is required." if not name else None,
            "sku": "SKU is required." if not sku else None,
        })

    tenant_id = g.user["tenant_id"]
    existing = fetch_one("SELECT id FROM products WHERE tenant_id = %s AND sku = %s;", (tenant_id, sku))
    if existing:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"sku": "SKU must be unique."})

    now = now_iso()
    opening_stock = int(data.get("openingStock", 0))

    pid = insert_and_get_id("products", {
        "tenant_id": tenant_id,
        "sku": sku[:60],
        "name": name[:160],
        "category": data.get("category"),
        "unit": data.get("unit", "pcs"),
        "cost_price": float(data.get("costPrice", 0)),
        "sale_price": float(data.get("salePrice", 0)),
        "quantity": opening_stock,
        "reorder_level": int(data.get("reorderLevel", 0)),
        "description": data.get("description"),
        "is_active": True,
        "created_at": now,
        "updated_at": now
    })

    if opening_stock > 0:
        insert_and_get_id("stock_movements", {
            "tenant_id": tenant_id,
            "product_id": pid,
            "user_id": g.user["id"],
            "type": "in",
            "quantity": opening_stock,
            "reason": "Opening stock",
            "created_at": now
        })

    row = fetch_one("SELECT * FROM products WHERE id = %s;", (pid,))
    return json_response(format_product(row), status=201)

@inventory_bp.route("/products/<pid>", methods=["GET"])
@require_auth
@require_feature("inventory")
@require_permission("inventory.view")
def get_product(pid):
    prod_id = parse_id(pid)
    row = fetch_one("SELECT * FROM products WHERE id = %s AND tenant_id = %s;", (prod_id, g.user["tenant_id"]))
    if not row:
        raise HttpError(404, "Product not found.")
    return json_response(format_product(row))

@inventory_bp.route("/products/<pid>/movements", methods=["POST"])
@require_auth
@require_feature("inventory")
@require_permission("inventory.stock.move")
def record_movement(pid):
    prod_id = parse_id(pid)
    tenant_id = g.user["tenant_id"]
    product = fetch_one("SELECT * FROM products WHERE id = %s AND tenant_id = %s;", (prod_id, tenant_id))
    if not product:
        raise HttpError(404, "Product not found.")

    data = request.get_json(silent=True) or {}
    m_type = data.get("type", "in") # in, out, adjust
    qty = int(data.get("quantity", 0))
    current_qty = int(product.get("quantity", 0))

    if m_type == "in":
        new_qty = current_qty + qty
    elif m_type == "out":
        if current_qty < qty:
            raise HttpError(422, "Insufficient stock on hand.", code="INSUFFICIENT_STOCK")
        new_qty = current_qty - qty
    elif m_type == "adjust":
        new_qty = qty
    else:
        raise HttpError(400, "Invalid movement type.")

    now = now_iso()
    insert_and_get_id("stock_movements", {
        "tenant_id": tenant_id,
        "product_id": prod_id,
        "user_id": g.user["id"],
        "type": m_type,
        "quantity": qty,
        "reason": data.get("reason"),
        "created_at": now
    })

    execute("UPDATE products SET quantity = %s, updated_at = %s WHERE id = %s;", (new_qty, now, prod_id))
    product["quantity"] = new_qty
    return json_response(format_product(product))
