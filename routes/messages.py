from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

messages_bp = Blueprint("messages", __name__, url_prefix="/api/messages")

@messages_bp.route("/conversations", methods=["GET"])
@require_auth
@require_feature("messaging")
@require_permission("messaging.use")
def get_conversations():
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]

    query = """
        SELECT c.*, m.body as last_message, m.created_at as last_message_time,
               u.name as other_user_name
        FROM conversations c
        JOIN conversation_members cm ON cm.conversation_id = c.id AND cm.user_id = %s
        LEFT JOIN messages m ON m.conversation_id = c.id AND m.id = (
            SELECT id FROM messages WHERE conversation_id = c.id ORDER BY id DESC LIMIT 1
        )
        LEFT JOIN conversation_members cm_other ON cm_other.conversation_id = c.id AND cm_other.user_id != %s
        LEFT JOIN users u ON u.id = cm_other.user_id
        WHERE c.tenant_id = %s
        ORDER BY COALESCE(c.last_message_at, c.created_at) DESC;
    """
    rows = fetch_all(query, (user_id, user_id, tenant_id))

    return json_response([
        {
            "id": r["id"],
            "type": r.get("type", "direct"),
            "name": r.get("name") or r.get("other_user_name") or "Direct Message",
            "lastMessage": r.get("last_message"),
            "lastMessageAt": r.get("last_message_time") or r.get("last_message_at"),
            "createdAt": r.get("created_at")
        }
        for r in rows
    ])

@messages_bp.route("/conversations", methods=["POST"])
@require_auth
@require_feature("messaging")
@require_permission("messaging.use")
def create_conversation():
    data = request.get_json(silent=True) or {}
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]
    c_type = data.get("type", "direct")
    now = now_iso()

    if c_type == "direct":
        recipient_id = parse_id(data.get("recipientId") or data.get("userId"))
        # Check existing direct
        existing = fetch_one("""
            SELECT c.id FROM conversations c
            JOIN conversation_members cm1 ON cm1.conversation_id = c.id AND cm1.user_id = %s
            JOIN conversation_members cm2 ON cm2.conversation_id = c.id AND cm2.user_id = %s
            WHERE c.tenant_id = %s AND c.type = 'direct'
            LIMIT 1;
        """, (user_id, recipient_id, tenant_id))
        if existing:
            return json_response({"id": existing["id"], "type": "direct"})

        cid = insert_and_get_id("conversations", {
            "tenant_id": tenant_id,
            "type": "direct",
            "created_at": now
        })
        insert_and_get_id("conversation_members", {"conversation_id": cid, "user_id": user_id, "role": "member", "joined_at": now}, returning="conversation_id")
        insert_and_get_id("conversation_members", {"conversation_id": cid, "user_id": recipient_id, "role": "member", "joined_at": now}, returning="conversation_id")
        return json_response({"id": cid, "type": "direct"}, status=201)

    # Group conversation
    name = (data.get("name") or "").strip()
    if not name:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Group name is required."})

    cid = insert_and_get_id("conversations", {
        "tenant_id": tenant_id,
        "type": "group",
        "name": name[:80],
        "created_at": now
    })
    insert_and_get_id("conversation_members", {"conversation_id": cid, "user_id": user_id, "role": "owner", "joined_at": now}, returning="conversation_id")
    for mid in data.get("memberIds", []):
        insert_and_get_id("conversation_members", {"conversation_id": cid, "user_id": parse_id(mid), "role": "member", "joined_at": now}, returning="conversation_id")

    return json_response({"id": cid, "type": "group", "name": name}, status=201)

@messages_bp.route("/conversations/<cid>/messages", methods=["GET"])
@require_auth
@require_feature("messaging")
@require_permission("messaging.use")
def get_messages(cid):
    conv_id = parse_id(cid)
    user_id = g.user["id"]
    membership = fetch_one("SELECT * FROM conversation_members WHERE conversation_id = %s AND user_id = %s;", (conv_id, user_id))
    if not membership:
        raise HttpError(404, "Conversation not found.")

    query = """
        SELECT m.*, u.name as sender_name
        FROM messages m
        LEFT JOIN users u ON u.id = m.sender_id
        WHERE m.conversation_id = %s
        ORDER BY m.id ASC
        LIMIT 100;
    """
    rows = fetch_all(query, (conv_id,))
    return json_response([
        {
            "id": r["id"],
            "conversationId": r["conversation_id"],
            "senderId": r.get("sender_id"),
            "senderName": r.get("sender_name") or "System",
            "body": r["body"],
            "kind": r.get("kind", "user"),
            "createdAt": r.get("created_at")
        }
        for r in rows
    ])

@messages_bp.route("/conversations/<cid>/messages", methods=["POST"])
@require_auth
@require_feature("messaging")
@require_permission("messaging.use")
def send_message(cid):
    conv_id = parse_id(cid)
    user_id = g.user["id"]
    membership = fetch_one("SELECT * FROM conversation_members WHERE conversation_id = %s AND user_id = %s;", (conv_id, user_id))
    if not membership:
        raise HttpError(404, "Conversation not found.")

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or data.get("content") or "").strip()
    if not body:
        raise HttpError(422, "Message cannot be empty.", code="VALIDATION_ERROR")

    now = now_iso()
    mid = insert_and_get_id("messages", {
        "conversation_id": conv_id,
        "sender_id": user_id,
        "body": body[:5000],
        "kind": "user",
        "created_at": now
    })

    execute("UPDATE conversations SET last_message_at = %s WHERE id = %s;", (now, conv_id))

    return json_response({
        "id": mid,
        "conversationId": conv_id,
        "senderId": user_id,
        "senderName": g.user["name"],
        "body": body,
        "createdAt": now
    }, status=201)
