from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

tickets_bp = Blueprint("tickets", __name__, url_prefix="/api/tickets")

def format_ticket(row: dict) -> dict:
    tid = row["id"]
    return {
        "id": tid,
        "ticket_number": row.get("ticket_number") or f"TKT-{str(tid).zfill(4)}",
        "ticketNumber": row.get("ticket_number") or f"TKT-{str(tid).zfill(4)}",
        "subject": row["subject"],
        "description": row["description"],
        "category": row.get("category", "other"),
        "priority": row.get("priority", "medium"),
        "status": row.get("status", "open"),
        "requesterId": row.get("requester_id"),
        "requester": {
            "id": row.get("requester_id"),
            "name": row.get("requester_name"),
            "email": row.get("requester_email")
        } if row.get("requester_name") else None,
        "assigneeId": row.get("assignee_id"),
        "assignee": {
            "id": row.get("assignee_id"),
            "name": row.get("assignee_name"),
            "email": row.get("assignee_email")
        } if row.get("assignee_name") else None,
        "teamId": row.get("team_id"),
        "team": {
            "id": row.get("team_id"),
            "name": row.get("team_name"),
            "color": row.get("team_color")
        } if row.get("team_name") else None,
        "createdAt": row.get("created_at"),
        "created_at": row.get("created_at"),
        "updatedAt": row.get("updated_at")
    }

@tickets_bp.route("", methods=["GET"])
@require_auth
@require_feature("helpdesk")
@require_permission("helpdesk.use", "helpdesk.work")
def get_tickets():
    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    is_staff = "helpdesk.work" in g.access["permissions"] or g.access["isOwner"]

    query = """
        SELECT tk.*,
               u_req.name as requester_name, u_req.email as requester_email,
               u_ass.name as assignee_name, u_ass.email as assignee_email,
               tm.name as team_name, tm.color as team_color
        FROM tickets tk
        LEFT JOIN users u_req ON u_req.id = tk.requester_id
        LEFT JOIN users u_ass ON u_ass.id = tk.assignee_id
        LEFT JOIN teams tm ON tm.id = tk.team_id
        WHERE tk.tenant_id = %s
    """
    params = [tenant_id]

    # Non-staff only see their own tickets
    if not is_staff:
        query += " AND tk.requester_id = %s"
        params.append(user_id)

    status = request.args.get("status")
    if status and status != "all":
        if status == "active":
            query += " AND tk.status IN ('open', 'in_progress', 'waiting')"
        else:
            query += " AND tk.status = %s"
            params.append(status)

    query += " ORDER BY tk.id DESC;"
    rows = fetch_all(query, tuple(params))
    return json_response([format_ticket(r) for r in rows])

@tickets_bp.route("", methods=["POST"])
@require_auth
@require_feature("helpdesk")
@require_permission("helpdesk.use", "helpdesk.work")
def create_ticket():
    data = request.get_json(silent=True) or {}
    subject = (data.get("subject") or data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not subject or not description:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={
            "subject": "Subject is required." if not subject else None,
            "description": "Description is required." if not description else None
        })

    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    now = now_iso()

    ticket_id = insert_and_get_id("tickets", {
        "tenant_id": tenant_id,
        "requester_id": user_id,
        "assignee_id": parse_id(data.get("assigneeId")) if data.get("assigneeId") else None,
        "team_id": parse_id(data.get("teamId")) if data.get("teamId") else None,
        "subject": subject[:200],
        "description": description[:10000],
        "category": data.get("category", "other"),
        "priority": data.get("priority", "medium"),
        "status": "open",
        "response_due_at": now,
        "resolution_due_at": now,
        "created_at": now,
        "updated_at": now
    })

    ticket_number = f"TKT-{str(ticket_id).zfill(4)}"
    execute("UPDATE tickets SET ticket_number = %s WHERE id = %s;", (ticket_number, ticket_id))

    row = fetch_one("""
        SELECT tk.*,
               u_req.name as requester_name, u_req.email as requester_email,
               u_ass.name as assignee_name, u_ass.email as assignee_email,
               tm.name as team_name, tm.color as team_color
        FROM tickets tk
        LEFT JOIN users u_req ON u_req.id = tk.requester_id
        LEFT JOIN users u_ass ON u_ass.id = tk.assignee_id
        LEFT JOIN teams tm ON tm.id = tk.team_id
        WHERE tk.id = %s;
    """, (ticket_id,))
    return json_response(format_ticket(row), status=201)

@tickets_bp.route("/<ticket_id>", methods=["GET"])
@require_auth
@require_feature("helpdesk")
@require_permission("helpdesk.use", "helpdesk.work")
def get_ticket(ticket_id):
    tid = parse_id(ticket_id)
    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    is_staff = "helpdesk.work" in g.access["permissions"] or g.access["isOwner"]

    row = fetch_one("""
        SELECT tk.*,
               u_req.name as requester_name, u_req.email as requester_email,
               u_ass.name as assignee_name, u_ass.email as assignee_email,
               tm.name as team_name, tm.color as team_color
        FROM tickets tk
        LEFT JOIN users u_req ON u_req.id = tk.requester_id
        LEFT JOIN users u_ass ON u_ass.id = tk.assignee_id
        LEFT JOIN teams tm ON tm.id = tk.team_id
        WHERE tk.id = %s AND tk.tenant_id = %s;
    """, (tid, tenant_id))

    if not row:
        raise HttpError(404, "Ticket not found.")

    if not is_staff and row["requester_id"] != user_id:
        raise HttpError(403, "You can only view your own tickets.", code="FORBIDDEN")

    # Fetch comments / messages
    msg_query = """
        SELECT tc.*, u.name as author_name, u.email as author_email
        FROM ticket_comments tc
        LEFT JOIN users u ON u.id = tc.author_id
        WHERE tc.ticket_id = %s
    """
    if not is_staff:
        msg_query += " AND tc.is_internal = false"
    msg_query += " ORDER BY tc.id ASC;"

    comments = fetch_all(msg_query, (tid,))
    formatted_ticket = format_ticket(row)
    formatted_ticket["messages"] = [
        {
            "id": c["id"],
            "ticket_id": c["ticket_id"],
            "author_id": c.get("author_id"),
            "author": {"id": c.get("author_id"), "name": c.get("author_name")} if c.get("author_name") else None,
            "body": c["body"],
            "is_internal": bool(c.get("is_internal")),
            "created_at": c.get("created_at")
        }
        for c in comments
    ]
    return json_response(formatted_ticket)

@tickets_bp.route("/<ticket_id>", methods=["PATCH"])
@require_auth
@require_feature("helpdesk")
@require_permission("helpdesk.work")
def update_ticket(ticket_id):
    tid = parse_id(ticket_id)
    tenant_id = g.user["tenant_id"]

    existing = fetch_one("SELECT * FROM tickets WHERE id = %s AND tenant_id = %s;", (tid, tenant_id))
    if not existing:
        raise HttpError(404, "Ticket not found.")

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": now_iso()}

    if "status" in data:
        updates["status"] = data["status"]
    if "priority" in data:
        updates["priority"] = data["priority"]
    if "category" in data:
        updates["category"] = data["category"]
    if "assigneeId" in data or "assignee_id" in data:
        ass_id = data.get("assigneeId") or data.get("assignee_id")
        updates["assignee_id"] = parse_id(ass_id) if ass_id else None
    if "teamId" in data or "team_id" in data:
        tm_id = data.get("teamId") or data.get("team_id")
        updates["team_id"] = parse_id(tm_id) if tm_id else None

    set_clauses = [f'"{k}" = %s' for k in updates.keys()]
    values = list(updates.values()) + [tid, tenant_id]
    execute(f'UPDATE tickets SET {", ".join(set_clauses)} WHERE id = %s AND tenant_id = %s;', tuple(values))

    row = fetch_one("""
        SELECT tk.*,
               u_req.name as requester_name, u_req.email as requester_email,
               u_ass.name as assignee_name, u_ass.email as assignee_email,
               tm.name as team_name, tm.color as team_color
        FROM tickets tk
        LEFT JOIN users u_req ON u_req.id = tk.requester_id
        LEFT JOIN users u_ass ON u_ass.id = tk.assignee_id
        LEFT JOIN teams tm ON tm.id = tk.team_id
        WHERE tk.id = %s;
    """, (tid,))
    return json_response(format_ticket(row))

@tickets_bp.route("/<ticket_id>/messages", methods=["POST"])
@tickets_bp.route("/<ticket_id>/comments", methods=["POST"])
@require_auth
@require_feature("helpdesk")
@require_permission("helpdesk.use", "helpdesk.work")
def add_ticket_message(ticket_id):
    tid = parse_id(ticket_id)
    tenant_id = g.user["tenant_id"]
    user_id = g.user["id"]
    is_staff = "helpdesk.work" in g.access["permissions"] or g.access["isOwner"]

    existing = fetch_one("SELECT * FROM tickets WHERE id = %s AND tenant_id = %s;", (tid, tenant_id))
    if not existing:
        raise HttpError(404, "Ticket not found.")

    if not is_staff and existing["requester_id"] != user_id:
        raise HttpError(403, "You can only comment on your own tickets.")

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or data.get("message") or "").strip()
    if not body:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"body": "Write a message first."})

    is_internal = bool(data.get("isInternal", False)) and is_staff
    now = now_iso()

    comment_id = insert_and_get_id("ticket_comments", {
        "ticket_id": tid,
        "author_id": user_id,
        "body": body[:10000],
        "is_internal": is_internal,
        "created_at": now
    })

    execute("UPDATE tickets SET updated_at = %s WHERE id = %s;", (now, tid))

    return json_response({
        "id": comment_id,
        "ticket_id": tid,
        "author_id": user_id,
        "author": {"id": user_id, "name": g.user["name"]},
        "body": body,
        "is_internal": is_internal,
        "created_at": now
    }, status=201)
