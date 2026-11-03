from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature
from utils.helpers import HttpError, json_response, parse_id, now_iso

announcements_bp = Blueprint("announcements", __name__, url_prefix="/api/announcements")
broadcasts_bp = Blueprint("broadcasts", __name__, url_prefix="/api/broadcasts")

# ==========================================
# ANNOUNCEMENTS
# ==========================================

@announcements_bp.route("", methods=["GET"])
@require_auth
@require_feature("announcements")
def get_announcements():
    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]

    query = """
        SELECT a.*, u.name as author_name, ar.read_at
        FROM announcements a
        LEFT JOIN users u ON u.id = a.author_id
        LEFT JOIN announcement_reads ar ON ar.announcement_id = a.id AND ar.user_id = %s
        WHERE a.tenant_id = %s
        ORDER BY a.pinned DESC, a.id DESC;
    """
    rows = fetch_all(query, (user_id, tenant_id))

    return json_response([
        {
            "id": r["id"],
            "title": r["title"],
            "body": r["body"],
            "content": r["body"],
            "pinned": bool(r.get("pinned", False)),
            "is_pinned": bool(r.get("pinned", False)),
            "authorId": r.get("author_id"),
            "authorName": r.get("author_name"),
            "isRead": bool(r.get("read_at")),
            "createdAt": r.get("created_at")
        }
        for r in rows
    ])

@announcements_bp.route("", methods=["POST"])
@require_auth
@require_feature("announcements")
def create_announcement():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or data.get("content") or "").strip()

    if not title or not body:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={
            "title": "Title is required." if not title else None,
            "body": "Body is required." if not body else None
        })

    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    now = now_iso()

    aid = insert_and_get_id("announcements", {
        "tenant_id": tenant_id,
        "author_id": user_id,
        "title": title[:160],
        "body": body[:10000],
        "pinned": bool(data.get("pinned", False)),
        "audience": data.get("audience", "all"),
        "created_at": now
    })

    return json_response({
        "id": aid,
        "title": title,
        "body": body,
        "pinned": bool(data.get("pinned", False)),
        "authorName": g.user["name"],
        "createdAt": now
    }, status=201)

@announcements_bp.route("/<aid>/read", methods=["POST"])
@require_auth
def read_announcement(aid):
    ann_id = parse_id(aid)
    user_id = g.user["id"]
    now = now_iso()
    existing = fetch_one("SELECT id FROM announcement_reads WHERE announcement_id = %s AND user_id = %s;", (ann_id, user_id))
    if not existing:
        insert_and_get_id("announcement_reads", {
            "announcement_id": ann_id,
            "user_id": user_id,
            "read_at": now
        })
    return json_response({"ok": True, "readAt": now})

@announcements_bp.route("/<aid>", methods=["DELETE"])
@require_auth
@require_feature("announcements")
def delete_announcement(aid):
    ann_id = parse_id(aid)
    execute("DELETE FROM announcements WHERE id = %s AND tenant_id = %s;", (ann_id, g.user["tenant_id"]))
    return json_response({}, status=204)

# ==========================================
# BROADCASTS
# ==========================================

@broadcasts_bp.route("", methods=["GET"])
@require_auth
def get_broadcasts():
    user_id = g.user["id"]
    query = """
        SELECT b.*, r.read_at, r.dismissed
        FROM provider_broadcasts b
        LEFT JOIN provider_broadcast_reads r ON r.broadcast_id = b.id AND r.user_id = %s
        ORDER BY b.id DESC
        LIMIT 10;
    """
    try:
        rows = fetch_all(query, (user_id,))
    except Exception:
        rows = []

    return json_response({
        "items": [
            {
                "id": r["id"],
                "title": r.get("title"),
                "body": r.get("body"),
                "isRead": bool(r.get("read_at")),
                "dismissed": bool(r.get("dismissed", False)),
                "createdAt": r.get("created_at")
            }
            for r in rows
        ],
        "unread": sum(1 for r in rows if not r.get("read_at"))
    })
