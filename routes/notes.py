from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

notes_bp = Blueprint("notes", __name__, url_prefix="/api/notes")

def format_note(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "body": row.get("body") or row.get("content") or "",
        "content": row.get("body") or row.get("content") or "",
        "color": row.get("color", "yellow"),
        "font": row.get("font", "caveat"),
        "textSize": row.get("text_size", "md"),
        "pinned": bool(row.get("pinned", False)),
        "is_pinned": bool(row.get("pinned", False)),
        "authorId": row.get("user_id"),
        "author": {"id": row.get("user_id"), "name": row.get("author_name")} if row.get("author_name") else None,
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at")
    }

@notes_bp.route("", methods=["GET"])
@require_auth
@require_feature("notes")
@require_permission("notes.use")
def get_notes():
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]
    can_manage = "notes.manage" in g.access["permissions"] or g.access["isOwner"]

    query = """
        SELECT n.*, u.name as author_name
        FROM notes n
        LEFT JOIN users u ON u.id = n.user_id
        WHERE n.tenant_id = %s
    """
    params = [tenant_id]

    if not can_manage:
        query += " AND (n.user_id = %s OR n.id IN (SELECT note_id FROM note_shares WHERE user_id = %s OR is_public = true))"
        params.extend([user_id, user_id])

    query += " ORDER BY n.pinned DESC, n.id DESC;"
    rows = fetch_all(query, tuple(params))
    return json_response([format_note(r) for r in rows])

@notes_bp.route("", methods=["POST"])
@require_auth
@require_feature("notes")
@require_permission("notes.use")
def create_note():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or data.get("content") or "").strip()

    if not title and not body:
        raise HttpError(422, "Write a title or some text.", code="EMPTY_NOTE")

    now = now_iso()
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]

    note_id = insert_and_get_id("notes", {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "title": title[:160],
        "body": body[:20000],
        "color": data.get("color", "yellow"),
        "font": data.get("font", "caveat"),
        "text_size": data.get("textSize", "md"),
        "pinned": bool(data.get("pinned", False)),
        "created_at": now,
        "updated_at": now
    })

    row = fetch_one("""
        SELECT n.*, u.name as author_name
        FROM notes n
        LEFT JOIN users u ON u.id = n.user_id
        WHERE n.id = %s;
    """, (note_id,))
    return json_response(format_note(row), status=201)

@notes_bp.route("/<note_id>", methods=["GET"])
@require_auth
@require_feature("notes")
@require_permission("notes.use")
def get_note(note_id):
    nid = parse_id(note_id)
    tenant_id = g.user["tenant_id"]

    row = fetch_one("""
        SELECT n.*, u.name as author_name
        FROM notes n
        LEFT JOIN users u ON u.id = n.user_id
        WHERE n.id = %s AND n.tenant_id = %s;
    """, (nid, tenant_id))

    if not row:
        raise HttpError(404, "Note not found.")
    return json_response(format_note(row))

@notes_bp.route("/<note_id>", methods=["PATCH", "PUT"])
@require_auth
@require_feature("notes")
@require_permission("notes.use")
def update_note(note_id):
    nid = parse_id(note_id)
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]
    can_manage = "notes.manage" in g.access["permissions"] or g.access["isOwner"]

    existing = fetch_one("SELECT * FROM notes WHERE id = %s AND tenant_id = %s;", (nid, tenant_id))
    if not existing:
        raise HttpError(404, "Note not found.")

    if not can_manage and existing["user_id"] != user_id:
        raise HttpError(403, "You can only edit your own notes.")

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": now_iso()}

    if "title" in data:
        updates["title"] = (data.get("title") or "").strip()[:160]
    if "body" in data or "content" in data:
        updates["body"] = (data.get("body") or data.get("content") or "").strip()[:20000]
    if "color" in data:
        updates["color"] = data.get("color")
    if "font" in data:
        updates["font"] = data.get("font")
    if "textSize" in data:
        updates["text_size"] = data.get("textSize")
    if "pinned" in data:
        updates["pinned"] = bool(data.get("pinned"))

    set_clauses = [f'"{k}" = %s' for k in updates.keys()]
    values = list(updates.values()) + [nid]
    execute(f'UPDATE notes SET {", ".join(set_clauses)} WHERE id = %s;', tuple(values))

    row = fetch_one("""
        SELECT n.*, u.name as author_name
        FROM notes n
        LEFT JOIN users u ON u.id = n.user_id
        WHERE n.id = %s;
    """, (nid,))
    return json_response(format_note(row))

@notes_bp.route("/<note_id>", methods=["DELETE"])
@require_auth
@require_feature("notes")
@require_permission("notes.use")
def delete_note(note_id):
    nid = parse_id(note_id)
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]
    can_manage = "notes.manage" in g.access["permissions"] or g.access["isOwner"]

    existing = fetch_one("SELECT * FROM notes WHERE id = %s AND tenant_id = %s;", (nid, tenant_id))
    if not existing:
        raise HttpError(404, "Note not found.")

    if not can_manage and existing["user_id"] != user_id:
        raise HttpError(403, "You can only delete your own notes.")

    execute("DELETE FROM notes WHERE id = %s;", (nid,))
    return json_response({}, status=204)
