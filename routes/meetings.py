from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth
from utils.helpers import HttpError, json_response, parse_id, now_iso

meetings_bp = Blueprint("meetings", __name__, url_prefix="/api/meetings")

def format_meeting(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row.get("description"),
        "platform": row.get("platform", "google_meet"),
        "url": row.get("url"),
        "startAt": row.get("start_at"),
        "endAt": row.get("end_at"),
        "reminderMinutes": row.get("reminder_minutes", 5),
        "host": {"id": row.get("created_by"), "name": row.get("host_name")} if row.get("host_name") else None,
        "createdAt": row.get("created_at")
    }

@meetings_bp.route("", methods=["GET"])
@require_auth
def get_meetings():
    tenant_id = g.user["tenant_id"]
    query = """
        SELECT m.*, u.name as host_name
        FROM meetings m
        LEFT JOIN users u ON u.id = m.created_by
        WHERE m.tenant_id = %s
        ORDER BY m.start_at DESC;
    """
    rows = fetch_all(query, (tenant_id,))
    return json_response([format_meeting(r) for r in rows])

@meetings_bp.route("", methods=["POST"])
@require_auth
def create_meeting():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    url = (data.get("url") or "").strip()
    start_at = data.get("startAt")

    if not title or not url or not start_at:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={
            "title": "Meeting title is required." if not title else None,
            "url": "Meeting URL is required." if not url else None,
            "startAt": "Start time is required." if not start_at else None,
        })

    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    now = now_iso()

    mid = insert_and_get_id("meetings", {
        "tenant_id": tenant_id,
        "created_by": user_id,
        "title": title[:200],
        "description": data.get("description"),
        "platform": data.get("platform", "google_meet"),
        "url": url[:1000],
        "start_at": start_at,
        "end_at": data.get("endAt"),
        "reminder_minutes": int(data.get("reminderMinutes", 5)),
        "reminder_sent": False,
        "is_instant": bool(data.get("isInstant", False)),
        "created_at": now,
        "updated_at": now
    })

    row = fetch_one("SELECT m.*, u.name as host_name FROM meetings m LEFT JOIN users u ON u.id = m.created_by WHERE m.id = %s;", (mid,))
    return json_response(format_meeting(row), status=201)

@meetings_bp.route("/<mid>", methods=["DELETE"])
@require_auth
def delete_meeting(mid):
    meeting_id = parse_id(mid)
    execute("DELETE FROM meetings WHERE id = %s AND tenant_id = %s;", (meeting_id, g.user["tenant_id"]))
    return json_response({}, status=204)
