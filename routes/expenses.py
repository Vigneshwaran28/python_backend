from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

expenses_bp = Blueprint("expenses", __name__, url_prefix="/api/expenses")

def format_claim(row: dict) -> dict:
    cid = row["id"]
    return {
        "id": cid,
        "key": f"EXP-{str(cid).zfill(4)}",
        "title": row["title"],
        "category": row.get("category"),
        "amount": float(row.get("amount") or 0),
        "spentOn": row.get("spent_on"),
        "notes": row.get("notes"),
        "status": row.get("status", "pending"),
        "user": {
            "id": row.get("user_id"),
            "name": row.get("user_name"),
            "jobTitle": row.get("user_job_title")
        } if row.get("user_name") else None,
        "reviewer": {
            "id": row.get("reviewer_id"),
            "name": row.get("reviewer_name")
        } if row.get("reviewer_name") else None,
        "reviewNote": row.get("review_note"),
        "reviewedAt": row.get("reviewed_at"),
        "paidAt": row.get("paid_at"),
        "createdAt": row.get("created_at")
    }

@expenses_bp.route("", methods=["GET"])
@require_auth
@require_feature("expenses")
def get_expenses():
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]
    is_reviewer = any(p in g.access["permissions"] for p in ("expenses.approve", "expenses.pay")) or g.access["isOwner"]

    query = """
        SELECT e.*, u.name as user_name, u.job_title as user_job_title, r.name as reviewer_name
        FROM expenses e
        JOIN users u ON u.id = e.user_id
        LEFT JOIN users r ON r.id = e.reviewer_id
        WHERE e.tenant_id = %s
    """
    params = [tenant_id]

    if not is_reviewer:
        query += " AND e.user_id = %s"
        params.append(user_id)

    query += " ORDER BY e.id DESC;"
    rows = fetch_all(query, tuple(params))
    return json_response([format_claim(r) for r in rows])

@expenses_bp.route("", methods=["POST"])
@require_auth
@require_feature("expenses")
@require_permission("expenses.submit")
def create_expense():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    category = (data.get("category") or "").strip()
    amount = float(data.get("amount") or 0)
    spent_on = data.get("spentOn")

    if not title or not category or amount <= 0 or not spent_on:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={
            "title": "Title is required." if not title else None,
            "category": "Category is required." if not category else None,
            "amount": "Amount must be greater than zero." if amount <= 0 else None,
            "spentOn": "Date spent is required." if not spent_on else None,
        })

    now = now_iso()
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]

    eid = insert_and_get_id("expenses", {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "title": title[:160],
        "category": category[:60],
        "amount": amount,
        "spent_on": spent_on,
        "notes": data.get("notes"),
        "status": "pending",
        "created_at": now
    })

    row = fetch_one("""
        SELECT e.*, u.name as user_name, u.job_title as user_job_title
        FROM expenses e
        JOIN users u ON u.id = e.user_id
        WHERE e.id = %s;
    """, (eid,))
    return json_response(format_claim(row), status=201)

@expenses_bp.route("/<eid>/approve", methods=["PATCH", "POST"])
@require_auth
@require_feature("expenses")
@require_permission("expenses.approve")
def approve_expense(eid):
    exp_id = parse_id(eid)
    now = now_iso()
    execute("""
        UPDATE expenses
        SET status = 'approved', reviewer_id = %s, reviewed_at = %s
        WHERE id = %s AND tenant_id = %s;
    """, (g.user["id"], now, exp_id, g.user["tenant_id"]))
    return json_response({"id": exp_id, "status": "approved"})

@expenses_bp.route("/<eid>/reject", methods=["PATCH", "POST"])
@require_auth
@require_feature("expenses")
@require_permission("expenses.approve")
def reject_expense(eid):
    exp_id = parse_id(eid)
    data = request.get_json(silent=True) or {}
    now = now_iso()
    execute("""
        UPDATE expenses
        SET status = 'rejected', reviewer_id = %s, reviewed_at = %s, review_note = %s
        WHERE id = %s AND tenant_id = %s;
    """, (g.user["id"], now, data.get("note"), exp_id, g.user["tenant_id"]))
    return json_response({"id": exp_id, "status": "rejected"})

@expenses_bp.route("/<eid>/pay", methods=["PATCH", "POST"])
@require_auth
@require_feature("expenses")
@require_permission("expenses.pay")
def pay_expense(eid):
    exp_id = parse_id(eid)
    now = now_iso()
    execute("""
        UPDATE expenses
        SET status = 'paid', paid_at = %s
        WHERE id = %s AND tenant_id = %s;
    """, (now, exp_id, g.user["tenant_id"]))
    return json_response({"id": exp_id, "status": "paid"})
