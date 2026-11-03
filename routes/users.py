import json
import random
from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_permission
from utils.helpers import (
    HttpError,
    json_response,
    parse_id,
    now_iso,
    hash_password,
    safe_json_loads
)

users_bp = Blueprint("users", __name__, url_prefix="/api/users")
roles_bp = Blueprint("roles", __name__, url_prefix="/api/roles")

# ==========================================
# USERS MANAGEMENT
# ==========================================

@users_bp.route("", methods=["GET"])
@require_auth
@require_permission("workspace.users.manage")
def get_users():
    tenant_id = g.user["tenant_id"]
    query = """
        SELECT u.id, u.name, u.email, u.role, u.job_title, u.is_active,
               u.must_change_password, u.last_login_at, u.login_code,
               u.is_code_login_enabled, u.created_at
        FROM users u
        WHERE u.tenant_id = %s
        ORDER BY LOWER(u.name) ASC;
    """
    users = fetch_all(query, (tenant_id,))

    roles = fetch_all("""
        SELECT ur.user_id, r.id, r.key, r.name
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE r.tenant_id = %s;
    """, (tenant_id,))
    role_map = {}
    for r in roles:
        role_map.setdefault(r["user_id"], []).append({"id": r["id"], "key": r["key"], "name": r["name"]})

    return json_response([
        {
            "id": u["id"],
            "name": u["name"],
            "email": u["email"],
            "role": u.get("role", "member"),
            "roles": role_map.get(u["id"], []),
            "jobTitle": u.get("job_title"),
            "isActive": bool(u.get("is_active", True)),
            "mustChangePassword": bool(u.get("must_change_password")),
            "isCodeLoginEnabled": bool(u.get("is_code_login_enabled", True)),
            "loginCode": u.get("login_code"),
            "lastLoginAt": u.get("last_login_at"),
            "createdAt": u.get("created_at")
        }
        for u in users
    ])

@users_bp.route("", methods=["POST"])
@require_auth
@require_permission("workspace.users.manage")
def create_user():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role_key = data.get("role", "member")
    job_title = (data.get("jobTitle") or "").strip() or None

    if not name or not email or not password:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={
            "name": "Name is required." if not name else None,
            "email": "Email is required." if not email else None,
            "password": "Password is required." if not password else None,
        })

    tenant_id = g.user["tenant_id"]
    existing = fetch_one("SELECT id FROM users WHERE LOWER(email) = LOWER(%s) AND tenant_id = %s;", (email, tenant_id))
    if existing:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"email": "This email already has an account in this workspace."})

    now = now_iso()
    hashed = hash_password(password)

    num = random.randint(100000, 999999)
    login_code = f"TF-{num}"

    user_id = insert_and_get_id("users", {
        "tenant_id": tenant_id,
        "name": name,
        "email": email,
        "password_hash": hashed,
        "job_title": job_title,
        "role": role_key,
        "is_active": True,
        "must_change_password": False,
        "login_code": login_code,
        "is_code_login_enabled": True,
        "created_at": now
    })

    role_row = fetch_one("SELECT id FROM roles WHERE tenant_id = %s AND key = %s;", (tenant_id, role_key))
    if role_row:
        insert_and_get_id("user_roles", {"user_id": user_id, "role_id": role_row["id"]})

    # Register access code entry
    insert_and_get_id("tenant_access_codes", {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "tenant_name": g.tenant["name"],
        "tenant_slug": g.tenant["slug"],
        "code": login_code,
        "name": f"{name} Login Code",
        "type": "user_login",
        "is_active": True,
        "is_deleted": False,
        "usage_count": 0,
        "created_at": now,
        "updated_at": now
    })

    return json_response({
        "id": user_id,
        "name": name,
        "email": email,
        "role": role_key,
        "loginCode": login_code,
        "isActive": True,
        "createdAt": now
    }, status=201)

@users_bp.route("/<user_id>", methods=["PATCH"])
@require_auth
@require_permission("workspace.users.manage")
def update_user(user_id):
    uid = parse_id(user_id)
    tenant_id = g.user["tenant_id"]
    user = fetch_one("SELECT * FROM users WHERE id = %s AND tenant_id = %s;", (uid, tenant_id))
    if not user:
        raise HttpError(404, "User not found.")

    data = request.get_json(silent=True) or {}
    updates = {}
    if "name" in data:
        updates["name"] = (data.get("name") or "").strip()
    if "jobTitle" in data:
        updates["job_title"] = (data.get("jobTitle") or "").strip() or None
    if "isActive" in data:
        updates["is_active"] = bool(data["isActive"])
    if "isCodeLoginEnabled" in data:
        updates["is_code_login_enabled"] = bool(data["isCodeLoginEnabled"])

    if updates:
        set_clauses = [f'"{k}" = %s' for k in updates.keys()]
        values = list(updates.values()) + [uid]
        execute(f'UPDATE users SET {", ".join(set_clauses)} WHERE id = %s;', tuple(values))

    if "role" in data:
        role_key = data["role"]
        execute("DELETE FROM user_roles WHERE user_id = %s;", (uid,))
        role_row = fetch_one("SELECT id FROM roles WHERE tenant_id = %s AND key = %s;", (tenant_id, role_key))
        if role_row:
            insert_and_get_id("user_roles", {"user_id": uid, "role_id": role_row["id"]})
        execute("UPDATE users SET role = %s WHERE id = %s;", (role_key, uid))

    updated = fetch_one("SELECT * FROM users WHERE id = %s;", (uid,))
    return json_response({
        "id": updated["id"],
        "name": updated["name"],
        "email": updated["email"],
        "role": updated.get("role", "member"),
        "isActive": bool(updated.get("is_active", True)),
        "isCodeLoginEnabled": bool(updated.get("is_code_login_enabled", True))
    })

# ==========================================
# ROLES & PERMISSIONS
# ==========================================

@roles_bp.route("", methods=["GET"])
@require_auth
def get_roles():
    tenant_id = g.user["tenant_id"]
    roles = fetch_all("SELECT * FROM roles WHERE tenant_id = %s ORDER BY is_system DESC, id ASC;", (tenant_id,))
    counts = fetch_all("""
        SELECT role_id, COUNT(*) as cnt
        FROM user_roles ur
        JOIN users u ON u.id = ur.user_id
        WHERE u.tenant_id = %s AND u.is_active = true
        GROUP BY role_id;
    """, (tenant_id,))
    count_map = {c["role_id"]: int(c["cnt"]) for c in counts}

    return json_response([
        {
            "id": r["id"],
            "key": r.get("key"),
            "name": r["name"],
            "description": r.get("description"),
            "isSystem": bool(r.get("is_system")),
            "locked": r.get("key") == "owner",
            "permissions": safe_json_loads(r.get("permissions"), default=[]),
            "members": count_map.get(r["id"], 0),
            "updatedAt": r.get("updated_at")
        }
        for r in roles
    ])

@roles_bp.route("", methods=["POST"])
@require_auth
@require_permission("workspace.roles.manage")
def create_role():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Name is required."})

    tenant_id = g.user["tenant_id"]
    perms = data.get("permissions", [])
    now = now_iso()

    role_id = insert_and_get_id("roles", {
        "tenant_id": tenant_id,
        "name": name[:60],
        "description": data.get("description"),
        "is_system": False,
        "permissions": json.dumps(perms),
        "created_at": now,
        "updated_at": now
    })

    return json_response({
        "id": role_id,
        "name": name,
        "description": data.get("description"),
        "isSystem": False,
        "permissions": perms,
        "members": 0,
        "updatedAt": now
    }, status=201)

@roles_bp.route("/<role_id>", methods=["PATCH", "PUT"])
@require_auth
@require_permission("workspace.roles.manage")
def update_role(role_id):
    rid = parse_id(role_id)
    tenant_id = g.user["tenant_id"]
    role = fetch_one("SELECT * FROM roles WHERE id = %s AND tenant_id = %s;", (rid, tenant_id))
    if not role:
        raise HttpError(404, "Role not found.")
    if role.get("key") == "owner":
        raise HttpError(403, "The Owner role permissions cannot be edited.")

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": now_iso()}
    if "name" in data:
        updates["name"] = (data.get("name") or "").strip()[:60]
    if "description" in data:
        updates["description"] = data.get("description")
    if "permissions" in data:
        updates["permissions"] = json.dumps(data["permissions"])

    set_clauses = [f'"{k}" = %s' for k in updates.keys()]
    values = list(updates.values()) + [rid]
    execute(f'UPDATE roles SET {", ".join(set_clauses)} WHERE id = %s;', tuple(values))

    updated = fetch_one("SELECT * FROM roles WHERE id = %s;", (rid,))
    return json_response({
        "id": updated["id"],
        "key": updated.get("key"),
        "name": updated["name"],
        "description": updated.get("description"),
        "permissions": safe_json_loads(updated.get("permissions"), default=[]),
        "updatedAt": updated.get("updated_at")
    })

@roles_bp.route("/<role_id>", methods=["DELETE"])
@require_auth
@require_permission("workspace.roles.manage")
def delete_role(role_id):
    rid = parse_id(role_id)
    tenant_id = g.user["tenant_id"]
    role = fetch_one("SELECT * FROM roles WHERE id = %s AND tenant_id = %s;", (rid, tenant_id))
    if not role:
        raise HttpError(404, "Role not found.")
    if role.get("is_system"):
        raise HttpError(403, "Built-in system roles cannot be deleted.")

    execute("DELETE FROM roles WHERE id = %s AND tenant_id = %s;", (rid, tenant_id))
    return json_response({}, status=204)
