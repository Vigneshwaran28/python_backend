from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso, safe_json_loads

billing_bp = Blueprint("billing", __name__, url_prefix="/api/billing")

def format_invoice(row: dict, items: list = None, payments: list = None) -> dict:
    inv_id = row["id"]
    return {
        "id": inv_id,
        "number": row.get("number") or f"INV-{str(inv_id).zfill(4)}",
        "kind": row.get("kind", "invoice"),
        "status": row.get("status", "draft"),
        "customerId": row.get("customer_id"),
        "customerName": row.get("customer_name") or (safe_json_loads(row.get("bill_to")).get("name") if row.get("bill_to") else None),
        "billTo": safe_json_loads(row.get("bill_to")),
        "issueDate": row.get("issue_date"),
        "dueDate": row.get("due_date"),
        "subtotal": float(row.get("subtotal") or 0),
        "tax": float(row.get("tax_total") or row.get("tax") or 0),
        "total": float(row.get("total") or 0),
        "paid": float(row.get("paid_total") or 0),
        "balance": float(row.get("total") or 0) - float(row.get("paid_total") or 0),
        "notes": row.get("notes"),
        "items": items or [],
        "payments": payments or [],
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at")
    }

@billing_bp.route("/invoices", methods=["GET"])
@require_auth
@require_feature("billing")
@require_permission("billing.view")
def get_invoices():
    tenant_id = g.user["tenant_id"]
    query = """
        SELECT i.*, c.name as customer_name
        FROM invoices i
        LEFT JOIN customers c ON c.id = i.customer_id
        WHERE i.tenant_id = %s
        ORDER BY i.id DESC;
    """
    rows = fetch_all(query, (tenant_id,))
    return json_response([format_invoice(r) for r in rows])

@billing_bp.route("/invoices", methods=["POST"])
@require_auth
@require_feature("billing")
@require_permission("billing.edit")
def create_invoice():
    data = request.get_json(silent=True) or {}
    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    now = now_iso()

    kind = data.get("kind", "invoice")
    prefix = "QT" if kind == "quote" else "INV"

    subtotal = float(data.get("subtotal") or 0)
    tax = float(data.get("tax") or 0)
    total = float(data.get("total") or (subtotal + tax))

    inv_id = insert_and_get_id("invoices", {
        "tenant_id": tenant_id,
        "created_by": user_id,
        "kind": kind,
        "customer_id": parse_id(data.get("customerId")) if data.get("customerId") else None,
        "number": f"{prefix}-{random_invoice_suffix()}",
        "status": "draft",
        "issue_date": data.get("issueDate"),
        "due_date": data.get("dueDate"),
        "subtotal": subtotal,
        "tax_total": tax,
        "total": total,
        "paid_total": 0,
        "notes": data.get("notes"),
        "bill_to": json.dumps(data.get("billTo")) if data.get("billTo") else None,
        "created_at": now,
        "updated_at": now
    })

    # Number format
    num_str = f"{prefix}-{str(inv_id).zfill(4)}"
    execute("UPDATE invoices SET number = %s WHERE id = %s;", (num_str, inv_id))

    row = fetch_one("SELECT * FROM invoices WHERE id = %s;", (inv_id,))
    return json_response(format_invoice(row), status=201)

def random_invoice_suffix():
    import random
    return str(random.randint(1000, 9999))

@billing_bp.route("/invoices/<inv_id>", methods=["GET"])
@require_auth
@require_feature("billing")
@require_permission("billing.view")
def get_invoice(inv_id):
    iid = parse_id(inv_id)
    tenant_id = g.user["tenant_id"]
    row = fetch_one("""
        SELECT i.*, c.name as customer_name
        FROM invoices i
        LEFT JOIN customers c ON c.id = i.customer_id
        WHERE i.id = %s AND i.tenant_id = %s;
    """, (iid, tenant_id))

    if not row:
        raise HttpError(404, "Invoice not found.")

    items = fetch_all("SELECT * FROM invoice_items WHERE invoice_id = %s ORDER BY id ASC;", (iid,))
    payments = fetch_all("SELECT * FROM invoice_payments WHERE invoice_id = %s ORDER BY id ASC;", (iid,))

    return json_response(format_invoice(row, items=items, payments=payments))

@billing_bp.route("/invoices/<inv_id>", methods=["PATCH", "PUT"])
@require_auth
@require_feature("billing")
@require_permission("billing.edit")
def update_invoice(inv_id):
    iid = parse_id(inv_id)
    tenant_id = g.user["tenant_id"]
    existing = fetch_one("SELECT * FROM invoices WHERE id = %s AND tenant_id = %s;", (iid, tenant_id))
    if not existing:
        raise HttpError(404, "Invoice not found.")

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": now_iso()}

    if "status" in data:
        updates["status"] = data["status"]
    if "notes" in data:
        updates["notes"] = data["notes"]
    if "dueDate" in data:
        updates["due_date"] = data["dueDate"]

    set_clauses = [f'"{k}" = %s' for k in updates.keys()]
    values = list(updates.values()) + [iid]
    execute(f'UPDATE invoices SET {", ".join(set_clauses)} WHERE id = %s;', tuple(values))

    return get_invoice(inv_id)

@billing_bp.route("/invoices/<inv_id>/payments", methods=["POST"])
@require_auth
@require_feature("billing")
@require_permission("billing.payments.record")
def record_payment(inv_id):
    iid = parse_id(inv_id)
    tenant_id = g.user["tenant_id"]
    existing = fetch_one("SELECT * FROM invoices WHERE id = %s AND tenant_id = %s;", (iid, tenant_id))
    if not existing:
        raise HttpError(404, "Invoice not found.")

    data = request.get_json(silent=True) or {}
    amount = float(data.get("amount") or 0)
    if amount <= 0:
        raise HttpError(422, "Payment amount must be greater than zero.", code="VALIDATION_ERROR")

    now = now_iso()
    pid = insert_and_get_id("invoice_payments", {
        "invoice_id": iid,
        "amount": amount,
        "payment_date": data.get("paymentDate") or data.get("date") or now[:10],
        "method": data.get("method", "bank_transfer"),
        "reference": data.get("reference"),
        "notes": data.get("notes"),
        "created_at": now
    })

    new_paid = float(existing.get("paid_total") or 0) + amount
    total = float(existing.get("total") or 0)
    new_status = "paid" if new_paid >= total else "partially_paid"

    execute("UPDATE invoices SET paid_total = %s, status = %s, updated_at = %s WHERE id = %s;", (new_paid, new_status, now, iid))

    return json_response({
        "id": pid,
        "invoiceId": iid,
        "amount": amount,
        "status": new_status,
        "createdAt": now
    }, status=201)

@billing_bp.route("/invoices/<inv_id>", methods=["DELETE"])
@require_auth
@require_feature("billing")
@require_permission("billing.delete")
def delete_invoice(inv_id):
    iid = parse_id(inv_id)
    tenant_id = g.user["tenant_id"]
    execute("DELETE FROM invoices WHERE id = %s AND tenant_id = %s AND status = 'draft';", (iid, tenant_id))
    return json_response({}, status=204)
