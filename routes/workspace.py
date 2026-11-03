import json
import random
from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_permission
from utils.helpers import HttpError, json_response, now_iso, safe_json_loads

workspace_bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")

@workspace_bp.route("/settings", methods=["GET"])
@require_auth
def get_settings():
    tenant_id = g.user["tenant_id"]
    tenant = fetch_one("SELECT * FROM tenants WHERE id = %s;", (tenant_id,))
    if not tenant:
        raise HttpError(404, "Workspace not found.")
    return json_response({
        "features": g.tenant["features"],
        "licensed": g.tenant["features"],
        "settings": safe_json_loads(tenant.get("settings"))
    })

@workspace_bp.route("/settings", methods=["PATCH"])
@require_auth
@require_permission("workspace.settings.manage")
def update_settings():
    tenant_id = g.user["tenant_id"]
    patch = request.get_json(silent=True) or {}
    tenant = fetch_one("SELECT * FROM tenants WHERE id = %s;", (tenant_id,))
    if not tenant:
        raise HttpError(404, "Workspace not found.")

    current = safe_json_loads(tenant.get("settings"))
    current.update(patch)

    now = now_iso()
    execute("UPDATE tenants SET settings = %s, updated_at = %s WHERE id = %s;", (json.dumps(current), now, tenant_id))

    return json_response({
        "features": g.tenant["features"],
        "licensed": g.tenant["features"],
        "settings": current
    })

@workspace_bp.route("/overview", methods=["GET"])
@require_auth
def get_overview():
    tenant_id = g.user["tenant_id"]
    tenant = fetch_one("SELECT * FROM tenants WHERE id = %s;", (tenant_id,))
    users = fetch_all("SELECT id, name, is_active, must_change_password, last_login_at, created_at FROM users WHERE tenant_id = %s;", (tenant_id,))

    active = [u for u in users if u.get("is_active")]
    roles = fetch_all("SELECT id, key, name, is_system FROM roles WHERE tenant_id = %s ORDER BY id;", (tenant_id,))

    return json_response({
        "workspace": {
            "name": tenant["name"],
            "slug": tenant["slug"],
            "status": tenant["status"],
            "createdAt": tenant.get("created_at")
        },
        "seats": {
            "used": len(active),
            "limit": tenant.get("max_users")
        },
        "people": {
            "total": len(users),
            "active": len(active),
            "deactivated": len(users) - len(active),
            "temporaryPassword": sum(1 for u in active if u.get("must_change_password")),
            "neverSignedIn": sum(1 for u in active if not u.get("last_login_at"))
        },
        "roles": [
            {"id": r["id"], "key": r.get("key"), "name": r["name"], "isSystem": bool(r.get("is_system")), "members": 0}
            for r in roles
        ],
        "modules": {
            "licensed": g.tenant["features"],
            "enabled": g.tenant["features"]
        },
        "security": safe_json_loads(tenant.get("settings")).get("security", {})
    })

@workspace_bp.route("/access-code", methods=["GET"])
@require_auth
@require_permission("workspace.settings.manage", "workspace.users.manage")
def get_access_code():
    tenant_id = g.user["tenant_id"]
    code = fetch_one(
        "SELECT * FROM tenant_access_codes WHERE tenant_id = %s AND is_deleted = false AND is_active = true ORDER BY id DESC LIMIT 1;",
        (tenant_id,)
    )
    if not code:
        # Generate default code if missing
        num = random.randint(100000, 999999)
        code_str = f"TF-{num}"
        now = now_iso()
        cid = insert_and_get_id("tenant_access_codes", {
            "tenant_id": tenant_id,
            "tenant_name": g.tenant["name"],
            "tenant_slug": g.tenant["slug"],
            "code": code_str,
            "name": "Default Security PIN",
            "type": "login_pin",
            "is_active": True,
            "is_deleted": False,
            "usage_count": 0,
            "created_at": now,
            "updated_at": now
        })
        code = fetch_one("SELECT * FROM tenant_access_codes WHERE id = %s;", (cid,))

    return json_response({
        "id": code["id"],
        "code": code["code"],
        "name": code["name"],
        "type": code["type"],
        "usageCount": code.get("usage_count", 0),
        "expiresAt": code.get("expires_at"),
        "createdAt": code.get("created_at")
    })

@workspace_bp.route("/access-code/regenerate", methods=["POST"])
@require_auth
@require_permission("workspace.settings.manage", "workspace.users.manage")
def regenerate_access_code():
    tenant_id = g.user["tenant_id"]
    execute("UPDATE tenant_access_codes SET is_deleted = true, is_active = false WHERE tenant_id = %s;", (tenant_id,))

    num = random.randint(100000, 999999)
    code_str = f"TF-{num}"
    now = now_iso()
    cid = insert_and_get_id("tenant_access_codes", {
        "tenant_id": tenant_id,
        "tenant_name": g.tenant["name"],
        "tenant_slug": g.tenant["slug"],
        "code": code_str,
        "name": "Security PIN",
        "type": "login_pin",
        "is_active": True,
        "is_deleted": False,
        "usage_count": 0,
        "created_at": now,
        "updated_at": now
    })
    code = fetch_one("SELECT * FROM tenant_access_codes WHERE id = %s;", (cid,))

    return json_response({
        "id": code["id"],
        "code": code["code"],
        "name": code["name"],
        "type": code["type"],
        "usageCount": 0,
        "createdAt": code.get("created_at")
    })
