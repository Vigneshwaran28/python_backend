from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

tasks_bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")
lists_bp = Blueprint("lists", __name__, url_prefix="/api/lists")

def format_task(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "notes": row.get("notes"),
        "description": row.get("notes"),
        "status": row.get("status", "todo"),
        "priority": row.get("priority", "medium"),
        "listId": row.get("list_id"),
        "list_id": row.get("list_id"),
        "list": {
            "id": row["list_id"],
            "name": row["list_name"],
            "color": row.get("list_color", "indigo")
        } if row.get("list_name") else None,
        "dueAt": row.get("due_at"),
        "due_date": row.get("due_at"),
        "remindAt": row.get("remind_at"),
        "reminderSent": bool(row.get("reminder_sent")),
        "repeat": row.get("repeat", "none"),
        "completedAt": row.get("completed_at"),
        "createdAt": row.get("created_at"),
        "created_at": row.get("created_at"),
        "updatedAt": row.get("updated_at")
    }

# ==========================================
# TASKS ROUTES
# ==========================================

@tasks_bp.route("", methods=["GET"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def get_tasks():
    user_id = g.user["id"]
    status = request.args.get("status")
    priority = request.args.get("priority")
    list_id = request.args.get("listId")
    search = request.args.get("search")

    query = """
        SELECT t.*, l.name as list_name, l.color as list_color
        FROM tasks t
        LEFT JOIN lists l ON l.id = t.list_id
        WHERE t.user_id = %s
    """
    params = [user_id]

    if status and status != "all":
        if status == "open":
            query += " AND t.status != 'done' AND t.status != 'completed'"
        elif status in ("done", "completed"):
            query += " AND (t.status = 'done' OR t.status = 'completed')"
        else:
            query += " AND t.status = %s"
            params.append(status)

    if priority:
        query += " AND t.priority = %s"
        params.append(priority)

    if list_id:
        query += " AND t.list_id = %s"
        params.append(parse_id(list_id))

    if search:
        query += " AND (LOWER(t.title) LIKE %s OR LOWER(COALESCE(t.notes, '')) LIKE %s)"
        term = f"%{search.lower()}%"
        params.extend([term, term])

    query += " ORDER BY t.due_at ASC NULLS LAST, t.id DESC;"
    rows = fetch_all(query, tuple(params))
    return json_response([format_task(r) for r in rows])

@tasks_bp.route("", methods=["POST"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def create_task():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"title": "Title is required."})

    user_id = g.user["id"]
    now = now_iso()
    status = data.get("status", "todo")
    if status == "completed":
        status = "done"

    list_id = data.get("listId") or data.get("list_id")
    if list_id is not None:
        list_id = parse_id(list_id)

    task_id = insert_and_get_id("tasks", {
        "user_id": user_id,
        "list_id": list_id,
        "title": title[:200],
        "notes": data.get("notes") or data.get("description"),
        "status": status,
        "priority": data.get("priority", "medium"),
        "due_at": data.get("dueAt") or data.get("due_date"),
        "remind_at": data.get("remindAt"),
        "repeat": data.get("repeat", "none"),
        "reminder_sent": False,
        "created_at": now,
        "updated_at": now
    })

    row = fetch_one("""
        SELECT t.*, l.name as list_name, l.color as list_color
        FROM tasks t
        LEFT JOIN lists l ON l.id = t.list_id
        WHERE t.id = %s;
    """, (task_id,))
    return json_response(format_task(row), status=201)

@tasks_bp.route("/<task_id>", methods=["GET"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def get_task(task_id):
    tid = parse_id(task_id)
    row = fetch_one("""
        SELECT t.*, l.name as list_name, l.color as list_color
        FROM tasks t
        LEFT JOIN lists l ON l.id = t.list_id
        WHERE t.id = %s AND t.user_id = %s;
    """, (tid, g.user["id"]))
    if not row:
        raise HttpError(404, "Task not found.")
    return json_response(format_task(row))

@tasks_bp.route("/<task_id>", methods=["PATCH", "PUT"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def update_task(task_id):
    tid = parse_id(task_id)
    user_id = g.user["id"]
    existing = fetch_one("SELECT * FROM tasks WHERE id = %s AND user_id = %s;", (tid, user_id))
    if not existing:
        raise HttpError(404, "Task not found.")

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": now_iso()}

    if "title" in data:
        updates["title"] = (data.get("title") or "").strip()[:200]
    if "notes" in data or "description" in data:
        updates["notes"] = data.get("notes") or data.get("description")
    if "status" in data:
        st = data.get("status")
        if st == "completed":
            st = "done"
        updates["status"] = st
        if st == "done":
            updates["completed_at"] = now_iso()
        else:
            updates["completed_at"] = None
    if "priority" in data:
        updates["priority"] = data.get("priority")
    if "listId" in data or "list_id" in data:
        lid = data.get("listId") if "listId" in data else data.get("list_id")
        updates["list_id"] = parse_id(lid) if lid is not None else None
    if "dueAt" in data or "due_date" in data:
        updates["due_at"] = data.get("dueAt") or data.get("due_date")
    if "remindAt" in data:
        updates["remind_at"] = data.get("remindAt")
        updates["reminder_sent"] = False

    set_clauses = [f'"{k}" = %s' for k in updates.keys()]
    values = list(updates.values()) + [tid, user_id]
    execute(f'UPDATE tasks SET {", ".join(set_clauses)} WHERE id = %s AND user_id = %s;', tuple(values))

    row = fetch_one("""
        SELECT t.*, l.name as list_name, l.color as list_color
        FROM tasks t
        LEFT JOIN lists l ON l.id = t.list_id
        WHERE t.id = %s;
    """, (tid,))
    return json_response(format_task(row))

@tasks_bp.route("/<task_id>", methods=["DELETE"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def delete_task(task_id):
    tid = parse_id(task_id)
    execute("DELETE FROM tasks WHERE id = %s AND user_id = %s;", (tid, g.user["id"]))
    return json_response({}, status=204)

# ==========================================
# LISTS ROUTES
# ==========================================

@lists_bp.route("", methods=["GET"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def get_lists():
    user_id = g.user["id"]
    query = """
        SELECT l.*, COUNT(t.id) FILTER (WHERE t.status != 'done' AND t.status != 'completed') as open_task_count
        FROM lists l
        LEFT JOIN tasks t ON t.list_id = l.id
        WHERE l.user_id = %s
        GROUP BY l.id
        ORDER BY l.name ASC;
    """
    rows = fetch_all(query, (user_id,))
    return json_response([
        {
            "id": r["id"],
            "name": r["name"],
            "color": r.get("color", "indigo"),
            "openTaskCount": int(r.get("open_task_count") or 0),
            "task_count": int(r.get("open_task_count") or 0),
            "createdAt": r.get("created_at")
        }
        for r in rows
    ])

@lists_bp.route("", methods=["POST"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def create_list():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Name is required."})

    user_id = g.user["id"]
    now = now_iso()
    list_id = insert_and_get_id("lists", {
        "user_id": user_id,
        "name": name[:80],
        "color": data.get("color", "indigo"),
        "created_at": now
    })

    return json_response({
        "id": list_id,
        "name": name,
        "color": data.get("color", "indigo"),
        "openTaskCount": 0,
        "task_count": 0,
        "createdAt": now
    }, status=201)

@lists_bp.route("/<list_id>", methods=["PATCH", "PUT"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def update_list(list_id):
    lid = parse_id(list_id)
    user_id = g.user["id"]
    data = request.get_json(silent=True) or {}

    updates = {}
    if "name" in data:
        updates["name"] = (data.get("name") or "").strip()[:80]
    if "color" in data:
        updates["color"] = data.get("color")

    if updates:
        set_clauses = [f'"{k}" = %s' for k in updates.keys()]
        values = list(updates.values()) + [lid, user_id]
        execute(f'UPDATE lists SET {", ".join(set_clauses)} WHERE id = %s AND user_id = %s;', tuple(values))

    row = fetch_one("SELECT * FROM lists WHERE id = %s AND user_id = %s;", (lid, user_id))
    if not row:
        raise HttpError(404, "List not found.")
    return json_response({
        "id": row["id"],
        "name": row["name"],
        "color": row.get("color"),
        "createdAt": row.get("created_at")
    })

@lists_bp.route("/<list_id>", methods=["DELETE"])
@require_auth
@require_feature("tasks")
@require_permission("tasks.use")
def delete_list(list_id):
    lid = parse_id(list_id)
    execute("DELETE FROM lists WHERE id = %s AND user_id = %s;", (lid, g.user["id"]))
    return json_response({}, status=204)
