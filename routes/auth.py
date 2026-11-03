import json
from flask import Blueprint, request, g
from config import Config
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import (
    require_auth,
    start_session,
    end_session,
    end_all_sessions,
    find_user_with_tenant,
    check_access_problem,
    load_access,
    parse_features,
    requested_workspace
)
from middleware.rate_limit import auth_limiter
from utils.helpers import (
    HttpError,
    json_response,
    now_iso,
    check_password,
    hash_password,
    validate_login_input,
    safe_json_loads
)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

def format_user_profile(user_row: dict) -> dict:
    pref = safe_json_loads(user_row.get("preferences"))
    avatar_url = pref.get("avatarUrl") if isinstance(pref, dict) else None
    return {
        "id": user_row["id"],
        "name": user_row["name"],
        "email": user_row["email"],
        "jobTitle": user_row.get("job_title"),
        "dateOfBirth": user_row.get("date_of_birth"),
        "joinedOn": user_row.get("joined_on"),
        "showBirthday": bool(user_row.get("show_birthday", True)),
        "avatarUrl": avatar_url,
        "preferences": pref,
        "createdAt": user_row.get("created_at")
    }

def format_session_data(user_row: dict, tenant_data: dict, access: dict, is_code_login: bool = False) -> dict:
    profile = format_user_profile(user_row)
    return {
        **profile,
        "tenant": tenant_data,
        "roles": access["roles"],
        "permissions": access["permissions"],
        "isOwner": access["isOwner"],
        "mustChangePassword": bool(user_row.get("must_change_password")),
        "isCodeLogin": bool(is_code_login)
    }

@auth_bp.route("/register", methods=["POST"])
def register_disabled():
    raise HttpError(403, "Accounts are created by your workspace administrator or joined using a workspace security PIN.", code="REGISTRATION_DISABLED")

@auth_bp.route("/verify-code", methods=["POST"])
@auth_limiter
def verify_code():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"code": "Enter a security code or PIN."})

    query = """
        SELECT tac.*, u.name as user_name, u.email as user_email
        FROM tenant_access_codes tac
        LEFT JOIN users u ON u.id = tac.user_id
        WHERE tac.code = %s AND tac.is_deleted = false AND tac.is_active = true
        LIMIT 1;
    """
    row = fetch_one(query, (code,))
    if not row:
        raise HttpError(404, "Invalid or expired security code / PIN.", code="INVALID_ACCESS_CODE")

    return json_response({
        "valid": True,
        "type": row.get("type", "tenant"),
        "code": row["code"],
        "user": {"id": row["user_id"], "name": row["user_name"], "email": row["user_email"]} if row.get("user_id") else None,
        "tenant": {
            "id": row["tenant_id"],
            "name": row["tenant_name"],
            "slug": row["tenant_slug"]
        }
    })

@auth_bp.route("/login", methods=["POST"])
@auth_limiter
def login():
    data = request.get_json(silent=True) or {}
    validated = validate_login_input(data)

    email = validated["email"]
    password = validated["password"]
    workspace = validated["workspace"]
    code = validated["code"]

    code_entry = None
    target_workspace = workspace

    if code:
        code_entry = fetch_one(
            "SELECT * FROM tenant_access_codes WHERE code = %s AND is_deleted = false AND is_active = true LIMIT 1;",
            (code,)
        )
        if not code_entry:
            raise HttpError(401, "Invalid or expired security code / PIN.", code="INVALID_ACCESS_CODE")
        target_workspace = code_entry["tenant_slug"]

    # Query accounts by email matching workspace if given
    if target_workspace:
        query = """
            SELECT u.*, t.name as tenant_name, t.slug as tenant_slug, t.status as tenant_status,
                   t.features as tenant_features, t.settings as tenant_settings, t.deleted_at as tenant_deleted_at,
                   t.maintenance as tenant_maintenance
            FROM users u
            JOIN tenants t ON t.id = u.tenant_id
            WHERE LOWER(u.email) = LOWER(%s) AND LOWER(t.slug) = LOWER(%s);
        """
        candidates = fetch_all(query, (email, target_workspace))
    else:
        query = """
            SELECT u.*, t.name as tenant_name, t.slug as tenant_slug, t.status as tenant_status,
                   t.features as tenant_features, t.settings as tenant_settings, t.deleted_at as tenant_deleted_at,
                   t.maintenance as tenant_maintenance
            FROM users u
            JOIN tenants t ON t.id = u.tenant_id
            WHERE LOWER(u.email) = LOWER(%s);
        """
        candidates = fetch_all(query, (email,))

    matches = []
    for c in candidates:
        if check_password(password, c.get("password_hash", "")):
            matches.append(c)

    if not matches:
        raise HttpError(401, "Incorrect email or password.", code="INVALID_CREDENTIALS")

    # Multi-tenant workspace selection if ambiguous
    usable = [m for m in matches if not check_access_problem(m)]
    if len(usable) > 1 and not target_workspace:
        workspaces = [
            {"slug": m["tenant_slug"], "siteTitle": safe_json_loads(m.get("tenant_settings")).get("branding", {}).get("siteTitle") or m["tenant_name"]}
            for m in usable
        ]
        return json_response({
            "message": "This email is in more than one workspace. Pick one to sign in to.",
            "code": "CHOOSE_WORKSPACE",
            "workspaces": workspaces
        }, status=409)

    selected_user = usable[0] if usable else matches[0]
    problem = check_access_problem(selected_user)
    if problem:
        raise HttpError(problem["status"], problem["message"], code=problem["code"])

    tenant_settings = safe_json_loads(selected_user.get("tenant_settings"))
    tenant_features = parse_features(selected_user.get("tenant_features"))
    access = load_access(selected_user["id"], tenant_features)

    tenant_data = {
        "id": selected_user["tenant_id"],
        "name": selected_user["tenant_name"],
        "slug": selected_user["tenant_slug"],
        "features": tenant_features,
        "settings": tenant_settings
    }

    # Record login timestamp
    now = now_iso()
    execute("UPDATE users SET last_login_at = %s WHERE id = %s;", (now, selected_user["id"]))

    if code_entry:
        execute("UPDATE tenant_access_codes SET usage_count = usage_count + 1, last_used_at = %s WHERE id = %s;", (now, code_entry["id"]))

    session_payload = format_session_data(selected_user, tenant_data, access, is_code_login=bool(code_entry))
    maintenance = {"active": False, "upcoming": None}

    resp = json_response({
        **session_payload,
        "maintenance": maintenance
    })

    token = start_session(
        response=resp,
        user_id=selected_user["id"],
        slug=selected_user["tenant_slug"],
        settings=tenant_settings,
        tenant_id=selected_user["tenant_id"],
        is_code_login=bool(code_entry)
    )

    # Attach token to response payload for mobile / API clients
    data = json.loads(resp.get_data(as_text=True))
    data["token"] = token
    resp.set_data(json.dumps(data))

    return resp

@auth_bp.route("/code-login", methods=["POST"])
@auth_limiter
def code_login():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"code": "Security code or PIN is required."})

    query = """
        SELECT tac.*, u.id as user_acc_id, u.name as user_name, u.email as user_email,
               u.password_hash, u.is_code_login_enabled
        FROM tenant_access_codes tac
        LEFT JOIN users u ON u.id = tac.user_id
        WHERE tac.code = %s AND tac.is_deleted = false AND tac.is_active = true
        LIMIT 1;
    """
    result = fetch_one(query, (code,))
    if not result:
        raise HttpError(401, "Invalid or expired security code / PIN.", code="INVALID_ACCESS_CODE")

    password = data.get("password")

    # If it's a personal user login code
    if result.get("user_acc_id"):
        user_row = find_user_with_tenant("u.id = %s", (result["user_acc_id"],))
        if not user_row:
            raise HttpError(404, "User account not found.", code="ACCOUNT_NOT_FOUND")

        if user_row.get("is_code_login_enabled") is False:
            raise HttpError(403, "Login with access code is disabled for this account.", code="USER_CODE_DISABLED")

        if user_row.get("password_hash") and password:
            if not check_password(password, user_row["password_hash"]):
                raise HttpError(401, "Incorrect password.", code="INVALID_CREDENTIALS")

        problem = check_access_problem(user_row)
        if problem:
            raise HttpError(problem["status"], problem["message"], code=problem["code"])

        tenant_settings = safe_json_loads(user_row.get("tenant_settings"))
        tenant_features = parse_features(user_row.get("tenant_features"))
        access = load_access(user_row["id"], tenant_features)

        tenant_data = {
            "id": user_row["tenant_id"],
            "name": user_row["tenant_name"],
            "slug": user_row["tenant_slug"],
            "features": tenant_features,
            "settings": tenant_settings
        }

        now = now_iso()
        execute("UPDATE users SET last_login_at = %s WHERE id = %s;", (now, user_row["id"]))
        execute("UPDATE tenant_access_codes SET usage_count = usage_count + 1, last_used_at = %s WHERE id = %s;", (now, result["id"]))

        session_payload = format_session_data(user_row, tenant_data, access, is_code_login=True)
        resp = json_response({**session_payload, "maintenance": {"active": False, "upcoming": None}})

        token = start_session(
            response=resp,
            user_id=user_row["id"],
            slug=user_row["tenant_slug"],
            settings=tenant_settings,
            tenant_id=user_row["tenant_id"],
            is_code_login=True
        )

        resp_data = json.loads(resp.get_data(as_text=True))
        resp_data["token"] = token
        resp.set_data(json.dumps(resp_data))
        return resp

    # Tenant-level PIN: if email provided, log into that account
    email = (data.get("email") or "").strip().lower()
    if email:
        user_row = find_user_with_tenant("LOWER(u.email) = LOWER(%s) AND u.tenant_id = %s", (email, result["tenant_id"]))
        if not user_row:
            raise HttpError(404, "No account found with this email in this workspace. You can create an account using this PIN.", code="ACCOUNT_NOT_FOUND")

        if not password:
            raise HttpError(400, "Password is required to sign in with this access code.", code="PASSWORD_REQUIRED")

        if not check_password(password, user_row.get("password_hash", "")):
            raise HttpError(401, "Incorrect password.", code="INVALID_CREDENTIALS")

        problem = check_access_problem(user_row)
        if problem:
            raise HttpError(problem["status"], problem["message"], code=problem["code"])

        tenant_settings = safe_json_loads(user_row.get("tenant_settings"))
        tenant_features = parse_features(user_row.get("tenant_features"))
        access = load_access(user_row["id"], tenant_features)

        tenant_data = {
            "id": user_row["tenant_id"],
            "name": user_row["tenant_name"],
            "slug": user_row["tenant_slug"],
            "features": tenant_features,
            "settings": tenant_settings
        }

        now = now_iso()
        execute("UPDATE users SET last_login_at = %s WHERE id = %s;", (now, user_row["id"]))
        execute("UPDATE tenant_access_codes SET usage_count = usage_count + 1, last_used_at = %s WHERE id = %s;", (now, result["id"]))

        session_payload = format_session_data(user_row, tenant_data, access, is_code_login=True)
        resp = json_response({**session_payload, "maintenance": {"active": False, "upcoming": None}})

        token = start_session(
            response=resp,
            user_id=user_row["id"],
            slug=user_row["tenant_slug"],
            settings=tenant_settings,
            tenant_id=user_row["tenant_id"],
            is_code_login=True
        )

        resp_data = json.loads(resp.get_data(as_text=True))
        resp_data["token"] = token
        resp.set_data(json.dumps(resp_data))
        return resp

    return json_response({
        "ok": True,
        "type": result.get("type", "tenant"),
        "code": result["code"],
        "user": None,
        "tenant": {
            "id": result["tenant_id"],
            "name": result["tenant_name"],
            "slug": result["tenant_slug"]
        }
    })

@auth_bp.route("/register-with-code", methods=["POST"])
@auth_limiter
def register_with_code():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    job_title = (data.get("jobTitle") or "").strip() or None

    if not code or not name or not email or not password:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={
            "name": "Name is required." if not name else None,
            "email": "Email is required." if not email else None,
            "password": "Password is required." if not password else None,
        })

    code_row = fetch_one(
        "SELECT * FROM tenant_access_codes WHERE code = %s AND is_deleted = false AND is_active = true LIMIT 1;",
        (code,)
    )
    if not code_row:
        raise HttpError(401, "Invalid or expired security code / PIN.", code="INVALID_ACCESS_CODE")

    tenant = fetch_one("SELECT * FROM tenants WHERE id = %s;", (code_row["tenant_id"],))
    if not tenant:
        raise HttpError(404, "Workspace not found.")
    if tenant.get("status") != "active":
        raise HttpError(403, "This workspace is currently suspended.", code="TENANT_SUSPENDED")

    existing_user = fetch_one(
        "SELECT id FROM users WHERE LOWER(email) = LOWER(%s) AND tenant_id = %s;",
        (email, tenant["id"])
    )
    if existing_user:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"email": "This email already has an account in this workspace."})

    now = now_iso()
    hashed = hash_password(password)
    user_id = insert_and_get_id("users", {
        "tenant_id": tenant["id"],
        "name": name,
        "email": email,
        "password_hash": hashed,
        "job_title": job_title,
        "role": "member",
        "is_active": True,
        "must_change_password": False,
        "created_at": now
    })

    # Assign member role
    member_role = fetch_one("SELECT id FROM roles WHERE tenant_id = %s AND key = 'member';", (tenant["id"],))
    if member_role:
        insert_and_get_id("user_roles", {"user_id": user_id, "role_id": member_role["id"]})

    execute("UPDATE tenant_access_codes SET usage_count = usage_count + 1, last_used_at = %s WHERE id = %s;", (now, code_row["id"]))

    user_row = find_user_with_tenant("u.id = %s", (user_id,))
    tenant_settings = safe_json_loads(user_row.get("tenant_settings"))
    tenant_features = parse_features(user_row.get("tenant_features"))
    access = load_access(user_id, tenant_features)

    tenant_data = {
        "id": tenant["id"],
        "name": tenant["name"],
        "slug": tenant["slug"],
        "features": tenant_features,
        "settings": tenant_settings
    }

    session_payload = format_session_data(user_row, tenant_data, access, is_code_login=True)
    resp = json_response({**session_payload, "maintenance": {"active": False, "upcoming": None}})

    token = start_session(
        response=resp,
        user_id=user_id,
        slug=tenant["slug"],
        settings=tenant_settings,
        tenant_id=tenant["id"],
        is_code_login=True
    )

    resp_data = json.loads(resp.get_data(as_text=True))
    resp_data["token"] = token
    resp.set_data(json.dumps(resp_data))
    return resp

@auth_bp.route("/me", methods=["GET"])
@require_auth
def get_me():
    user = g.user
    tenant = g.tenant
    access = g.access
    profile = format_user_profile(user)
    return json_response({
        **profile,
        "tenant": tenant,
        "roles": access["roles"],
        "permissions": access["permissions"],
        "isOwner": access["isOwner"],
        "mustChangePassword": user["must_change_password"],
        "isCodeLogin": user["is_code_login"],
        "maintenance": g.maintenance
    })

@auth_bp.route("/me", methods=["PATCH"])
@require_auth
def update_profile():
    data = request.get_json(silent=True) or {}
    user_id = g.user["id"]
    name = (data.get("name") or "").strip()
    if not name:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Name is required."})

    updates = {"name": name}
    if "jobTitle" in data:
        updates["job_title"] = (data.get("jobTitle") or "").strip() or None
    if "dateOfBirth" in data:
        updates["date_of_birth"] = data.get("dateOfBirth") or None
    if "showBirthday" in data:
        updates["show_birthday"] = bool(data.get("showBirthday"))

    if "avatarUrl" in data:
        current_user = fetch_one("SELECT preferences FROM users WHERE id = %s;", (user_id,))
        pref = safe_json_loads(current_user.get("preferences"))
        pref["avatarUrl"] = data.get("avatarUrl") or ""
        updates["preferences"] = json.dumps(pref)

    set_clauses = [f'"{k}" = %s' for k in updates.keys()]
    values = list(updates.values()) + [user_id]
    execute(f'UPDATE users SET {", ".join(set_clauses)} WHERE id = %s;', tuple(values))

    updated_user = find_user_with_tenant("u.id = %s", (user_id,))
    return json_response(format_session_data(updated_user, g.tenant, g.access, g.user["is_code_login"]))

@auth_bp.route("/me/preferences", methods=["PATCH"])
@require_auth
def update_preferences():
    data = request.get_json(silent=True) or {}
    user_id = g.user["id"]

    current_user = fetch_one("SELECT preferences FROM users WHERE id = %s;", (user_id,))
    pref = safe_json_loads(current_user.get("preferences"))
    pref.update(data)

    execute("UPDATE users SET preferences = %s WHERE id = %s;", (json.dumps(pref), user_id))
    return json_response(pref)

@auth_bp.route("/me/password", methods=["POST"])
@require_auth
def change_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get("currentPassword") or ""
    new_password = data.get("newPassword") or ""

    if len(new_password) < 8:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"newPassword": "Use at least 8 characters."})

    user_id = g.user["id"]
    user_row = fetch_one("SELECT password_hash FROM users WHERE id = %s;", (user_id,))

    # First-time code login with must_change_password doesn't require currentPassword
    is_first_time_code = bool(g.user.get("is_code_login") and g.user.get("must_change_password"))
    if not is_first_time_code:
        if not current_password or not check_password(current_password, user_row.get("password_hash", "")):
            raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"currentPassword": "Current password is incorrect."})

    now = now_iso()
    hashed = hash_password(new_password)
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = false, password_changed_at = %s WHERE id = %s;",
        (hashed, now, user_id)
    )

    resp = json_response({}, status=204)
    start_session(
        response=resp,
        user_id=user_id,
        slug=g.tenant["slug"],
        settings=g.tenant["settings"],
        tenant_id=g.tenant["id"],
        is_code_login=False
    )
    return resp

@auth_bp.route("/logout", methods=["POST"])
def logout():
    data = request.get_json(silent=True) or {}
    resp = json_response({}, status=204)
    if data.get("all") is True:
        end_all_sessions(resp)
    else:
        slug = requested_workspace() or data.get("workspace")
        end_session(resp, slug)
    return resp

@auth_bp.route("/sessions", methods=["GET"])
def list_sessions():
    """Returns active signed-in workspaces for switcher."""
    resp = json_response([])
    resp.headers["Cache-Control"] = "no-store"
    return resp
