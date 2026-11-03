from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute
from middleware.auth import require_auth
from utils.helpers import json_response, parse_id, now_iso

stats_bp = Blueprint("stats", __name__, url_prefix="/api/stats")
notifications_bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

@stats_bp.route("", methods=["GET"])
@require_auth
def get_stats():
    user_id = g.user["id"]
    now = now_iso()

    open_tasks = fetch_one("SELECT COUNT(*) as cnt FROM tasks WHERE user_id = %s AND status != 'done';", (user_id,))
    due_today = fetch_one("SELECT COUNT(*) as cnt FROM tasks WHERE user_id = %s AND status != 'done' AND due_at::text LIKE %s;", (user_id, f"{now[:10]}%"))
    overdue = fetch_one("SELECT COUNT(*) as cnt FROM tasks WHERE user_id = %s AND status != 'done' AND due_at < %s;", (user_id, now))

    by_priority_rows = fetch_all("SELECT priority, COUNT(*) as cnt FROM tasks WHERE user_id = %s AND status != 'done' GROUP BY priority;", (user_id,))
    priority_map = {"low": 0, "medium": 0, "high": 0, "urgent": 0}
    for r in by_priority_rows:
        if r["priority"] in priority_map:
            priority_map[r["priority"]] = int(r["cnt"])

    upcoming = fetch_all("""
        SELECT t.*, l.name as list_name, l.color as list_color
        FROM tasks t
        LEFT JOIN lists l ON l.id = t.list_id
        WHERE t.user_id = %s AND t.status != 'done' AND t.due_at IS NOT NULL
        ORDER BY t.due_at ASC
        LIMIT 6;
    """, (user_id,))

    return json_response({
        "open": int(open_tasks["cnt"] if open_tasks else 0),
        "dueToday": int(due_today["cnt"] if due_today else 0),
        "overdue": int(overdue["cnt"] if overdue else 0),
        "completedThisWeek": 0,
        "byPriority": priority_map,
        "completedAt": [],
        "nextUp": [
            {
                "id": u["id"],
                "title": u["title"],
                "dueAt": u.get("due_at"),
                "priority": u.get("priority"),
                "status": u.get("status"),
                "list": {"name": u.get("list_name"), "color": u.get("list_color")} if u.get("list_name") else None
            }
            for u in upcoming
        ]
    })

@notifications_bp.route("", methods=["GET"])
@require_auth
def get_notifications():
    user_id = g.user["id"]
    items = fetch_all("""
        SELECT * FROM notifications
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 30;
    """, (user_id,))

    unread_res = fetch_one("SELECT COUNT(*) as cnt FROM notifications WHERE user_id = %s AND is_read = false;", (user_id,))
    unread_count = int(unread_res["cnt"] if unread_res else 0)

    return json_response({
        "unreadCount": unread_count,
        "items": [
            {
                "id": n["id"],
                "taskId": n.get("task_id"),
                "ticketId": n.get("ticket_id"),
                "meetingId": n.get("meeting_id"),
                "title": n["title"],
                "body": n.get("body"),
                "isRead": bool(n.get("is_read", False)),
                "createdAt": n.get("created_at")
            }
            for n in items
        ]
    })

@notifications_bp.route("/read-all", methods=["POST"])
@require_auth
def read_all_notifications():
    execute("UPDATE notifications SET is_read = true WHERE user_id = %s AND is_read = false;", (g.user["id"],))
    return json_response({}, status=204)

@notifications_bp.route("/<nid>/read", methods=["POST"])
@require_auth
def read_notification(nid):
    notification_id = parse_id(nid)
    execute("UPDATE notifications SET is_read = true WHERE id = %s AND user_id = %s;", (notification_id, g.user["id"]))
    return json_response({}, status=204)

@notifications_bp.route("", methods=["DELETE"])
@require_auth
def clear_notifications():
    execute("DELETE FROM notifications WHERE user_id = %s;", (g.user["id"],))
    return json_response({}, status=204)
