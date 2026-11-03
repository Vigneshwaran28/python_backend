from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso, safe_json_loads

people_bp = Blueprint("people", __name__, url_prefix="/api/people")
teams_bp = Blueprint("teams", __name__, url_prefix="/api/teams")

# ==========================================
# PEOPLE DIRECTORY
# ==========================================

@people_bp.route("", methods=["GET"])
@require_auth
def get_people():
    tenant_id = g.user["tenant_id"]
    search = request.args.get("search")
    team_id = request.args.get("teamId")
    role_key = request.args.get("role")
    include_inactive = request.args.get("includeInactive") == "true"

    query = """
        SELECT u.id, u.name, u.email, u.job_title, u.is_active, u.date_of_birth,
               u.show_birthday, u.joined_on, u.created_at, u.preferences
        FROM users u
        WHERE u.tenant_id = %s
    """
    params = [tenant_id]

    if not include_inactive:
        query += " AND u.is_active = true"

    if search:
        term = f"%{search.lower()}%"
        query += " AND (LOWER(u.name) LIKE %s OR LOWER(u.email) LIKE %s OR LOWER(COALESCE(u.job_title, '')) LIKE %s)"
        params.extend([term, term, term])

    if team_id:
        query += " AND u.id IN (SELECT user_id FROM team_members WHERE team_id = %s)"
        params.append(parse_id(team_id))

    if role_key:
        query += " AND u.id IN (SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE r.tenant_id = %s AND r.key = %s)"
        params.extend([tenant_id, role_key])

    query += " ORDER BY LOWER(u.name) ASC;"
    users = fetch_all(query, tuple(params))

    # Fetch team memberships
    memberships = fetch_all("""
        SELECT tm.user_id, tm.is_lead, t.id as team_id, t.name as team_name, t.color as team_color
        FROM team_members tm
        JOIN teams t ON t.id = tm.team_id
        WHERE t.tenant_id = %s;
    """, (tenant_id,))

    team_map = {}
    for m in memberships:
        team_map.setdefault(m["user_id"], []).append({
            "id": m["team_id"],
            "name": m["team_name"],
            "color": m["team_color"],
            "isLead": bool(m["is_lead"])
        })

    # Fetch roles
    roles = fetch_all("""
        SELECT ur.user_id, r.id, r.key, r.name
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE r.tenant_id = %s;
    """, (tenant_id,))

    role_map = {}
    for r in roles:
        role_map.setdefault(r["user_id"], []).append({
            "id": r["id"],
            "key": r["key"],
            "name": r["name"]
        })

    result = []
    for u in users:
        pref = safe_json_loads(u.get("preferences"))
        avatar = pref.get("avatarUrl") if isinstance(pref, dict) else None
        bday = None
        if u.get("show_birthday") and u.get("date_of_birth"):
            bday = str(u["date_of_birth"])[5:]

        result.append({
            "id": u["id"],
            "name": u["name"],
            "email": u["email"],
            "roles": role_map.get(u["id"], []),
            "jobTitle": u.get("job_title"),
            "avatarUrl": avatar,
            "isActive": bool(u.get("is_active", True)),
            "birthday": bday,
            "joinedOn": u.get("joined_on"),
            "createdAt": u.get("created_at"),
            "teams": team_map.get(u["id"], [])
        })

    return json_response(result)

# ==========================================
# TEAMS MANAGEMENT
# ==========================================

def format_team(team: dict, members: list, open_tickets: int = 0) -> dict:
    return {
        "id": team["id"],
        "name": team["name"],
        "description": team.get("description"),
        "color": team.get("color", "indigo"),
        "createdAt": team.get("created_at"),
        "openTickets": open_tickets,
        "members": [
            {
                "id": m["user_id"],
                "name": m.get("name"),
                "email": m.get("email"),
                "jobTitle": m.get("job_title"),
                "isLead": bool(m.get("is_lead"))
            }
            for m in members if m.get("team_id") == team["id"]
        ]
    }

@teams_bp.route("", methods=["GET"])
@require_auth
@require_feature("directory", "helpdesk")
def get_teams():
    tenant_id = g.user["tenant_id"]
    teams = fetch_all("SELECT * FROM teams WHERE tenant_id = %s ORDER BY LOWER(name) ASC;", (tenant_id,))

    members = fetch_all("""
        SELECT tm.team_id, tm.user_id, tm.is_lead, u.name, u.email, u.job_title
        FROM team_members tm
        JOIN users u ON u.id = tm.user_id
        WHERE u.tenant_id = %s AND u.is_active = true
        ORDER BY tm.is_lead DESC, LOWER(u.name) ASC;
    """, (tenant_id,))

    ticket_counts = fetch_all("""
        SELECT team_id, COUNT(*) as cnt
        FROM tickets
        WHERE tenant_id = %s AND status IN ('open', 'in_progress', 'waiting') AND team_id IS NOT NULL
        GROUP BY team_id;
    """, (tenant_id,))
    ticket_map = {tc["team_id"]: int(tc["cnt"]) for tc in ticket_counts}

    return json_response([
        format_team(t, members, ticket_map.get(t["id"], 0))
        for t in teams
    ])

@teams_bp.route("", methods=["POST"])
@require_auth
@require_permission("directory.teams.manage")
def create_team():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Name is required."})

    tenant_id = g.user["tenant_id"]
    now = now_iso()
    team_id = insert_and_get_id("teams", {
        "tenant_id": tenant_id,
        "name": name[:80],
        "description": data.get("description"),
        "color": data.get("color", "indigo"),
        "created_at": now
    })

    members_input = data.get("members", [])
    for m in members_input:
        uid = parse_id(m.get("userId") or m.get("id"))
        insert_and_get_id("team_members", {
            "team_id": team_id,
            "user_id": uid,
            "is_lead": bool(m.get("isLead")),
            "created_at": now
        }, returning="team_id")

    team = fetch_one("SELECT * FROM teams WHERE id = %s;", (team_id,))
    return json_response(format_team(team, []), status=201)

@teams_bp.route("/<team_id>", methods=["GET"])
@require_auth
def get_team(team_id):
    tid = parse_id(team_id)
    tenant_id = g.user["tenant_id"]
    team = fetch_one("SELECT * FROM teams WHERE id = %s AND tenant_id = %s;", (tid, tenant_id))
    if not team:
        raise HttpError(404, "Team not found.")

    members = fetch_all("""
        SELECT tm.team_id, tm.user_id, tm.is_lead, u.name, u.email, u.job_title
        FROM team_members tm
        JOIN users u ON u.id = tm.user_id
        WHERE tm.team_id = %s AND u.is_active = true;
    """, (tid,))

    return json_response(format_team(team, members))

@teams_bp.route("/<team_id>", methods=["PATCH", "PUT"])
@require_auth
@require_permission("directory.teams.manage")
def update_team(team_id):
    tid = parse_id(team_id)
    tenant_id = g.user["tenant_id"]
    team = fetch_one("SELECT * FROM teams WHERE id = %s AND tenant_id = %s;", (tid, tenant_id))
    if not team:
        raise HttpError(404, "Team not found.")

    data = request.get_json(silent=True) or {}
    updates = {}
    if "name" in data:
        updates["name"] = (data.get("name") or "").strip()[:80]
    if "description" in data:
        updates["description"] = data.get("description")
    if "color" in data:
        updates["color"] = data.get("color")

    if updates:
        set_clauses = [f'"{k}" = %s' for k in updates.keys()]
        values = list(updates.values()) + [tid]
        execute(f'UPDATE teams SET {", ".join(set_clauses)} WHERE id = %s;', tuple(values))

    if "members" in data:
        execute("DELETE FROM team_members WHERE team_id = %s;", (tid,))
        now = now_iso()
        for m in data.get("members", []):
            uid = parse_id(m.get("userId") or m.get("id"))
            insert_and_get_id("team_members", {
                "team_id": tid,
                "user_id": uid,
                "is_lead": bool(m.get("isLead")),
                "created_at": now
            }, returning="team_id")

    return get_team(team_id)

@teams_bp.route("/<team_id>", methods=["DELETE"])
@require_auth
@require_permission("directory.teams.manage")
def delete_team(team_id):
    tid = parse_id(team_id)
    execute("DELETE FROM teams WHERE id = %s AND tenant_id = %s;", (tid, g.user["tenant_id"]))
    return json_response({}, status=204)
