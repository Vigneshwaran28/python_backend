import re
import uuid
import time
import json
import logging
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple
import jwt
from flask import request, g, Response
from config import Config
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from utils.helpers import HttpError, now_iso, safe_json_loads

logger = logging.getLogger(__name__)

SLUG_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$")

ALL_FEATURES = [
    "tasks", "helpdesk", "directory", "messaging", "announcements",
    "inventory", "sales", "billing", "logistics", "attendance",
    "expenses", "clinic", "notes"
]

def requested_workspace() -> Optional[str]:
    """Reads workspace slug from X-Workspace header or ?ws= query param."""
    val = (request.headers.get(Config.WORKSPACE_HEADER) or request.args.get("ws") or "").strip().lower()
    return val if SLUG_REGEX.match(val) else None

def workspace_cookie_name(slug: str) -> str:
    return f"{Config.WORKSPACE_COOKIE_PREFIX}{slug}"

def get_token_from_request(slug: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    Extracts JWT session token from:
    1. Authorization: Bearer <token>
    2. tf_s_<slug> cookie
    3. tf_session cookie
    Returns (token, cookie_name) or None.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            return token, "authorization_bearer"

    cookies = request.cookies
    if slug:
        own = cookies.get(workspace_cookie_name(slug))
        if own:
            return own, workspace_cookie_name(slug)

    session_cookie = cookies.get(Config.SESSION_COOKIE)
    if session_cookie:
        return session_cookie, Config.SESSION_COOKIE

    # Check any workspace cookie if only 1 exists
    ws_cookies = [
        (k[len(Config.WORKSPACE_COOKIE_PREFIX):], v)
        for k, v in cookies.items()
        if k.startswith(Config.WORKSPACE_COOKIE_PREFIX) and v
    ]
    if len(ws_cookies) == 1 and not slug:
        return ws_cookies[0][1], workspace_cookie_name(ws_cookies[0][0])

    return None

def verify_session_token(token: str) -> Dict[str, Any]:
    """Decodes and validates JWT token."""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
        if payload.get("aud") == "taskflow-admin":
            raise HttpError(401, "Invalid token audience for user session", code="UNAUTHENTICATED")
        return payload
    except jwt.ExpiredSignatureError:
        raise HttpError(401, "Your session has expired. Please sign in again.", code="UNAUTHENTICATED")
    except Exception:
        raise HttpError(401, "Please sign in.", code="UNAUTHENTICATED")

def find_user_with_tenant(where_clause: str, params: Tuple) -> Optional[Dict[str, Any]]:
    query = f"""
        SELECT 
            u.*,
            t.name as tenant_name,
            t.slug as tenant_slug,
            t.status as tenant_status,
            t.features as tenant_features,
            t.settings as tenant_settings,
            t.deleted_at as tenant_deleted_at,
            t.subscription_ends_on as tenant_ends_on,
            t.grace_days as tenant_grace_days,
            t.maintenance as tenant_maintenance
        FROM users u
        LEFT JOIN tenants t ON t.id = u.tenant_id
        WHERE {where_clause}
        LIMIT 1;
    """
    return fetch_one(query, params)

def parse_features(features_json: Optional[str]) -> List[str]:
    if not features_json:
        return []
    try:
        parsed = json.loads(features_json)
        if isinstance(parsed, list):
            return [f for f in ALL_FEATURES if f in parsed]
    except Exception:
        pass
    return []

def load_access(user_id: int, features: List[str]) -> Dict[str, Any]:
    query = """
        SELECT r.id, r.key, r.name, r.permissions
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = %s;
    """
    roles = fetch_all(query, (user_id,))
    is_owner = any(r.get("key") == "owner" for r in roles)

    permissions_set = set()
    for role in roles:
        raw_perms = role.get("permissions")
        perms_list = safe_json_loads(raw_perms, default=[])
        for p in perms_list:
            permissions_set.add(p)

    # Owner has all module permissions
    if is_owner:
        permissions_set.update([
            "tasks.use", "helpdesk.use", "helpdesk.work", "directory.view",
            "directory.teams.manage", "workspace.users.manage", "workspace.roles.manage",
            "workspace.settings.manage", "workspace.audit.view", "inventory.view",
            "inventory.products.edit", "inventory.stock.move", "inventory.products.delete",
            "sales.view", "sales.edit", "sales.orders.confirm", "sales.delete",
            "billing.view", "billing.edit", "billing.issue", "billing.payments.record",
            "billing.delete", "attendance.self", "attendance.team.view", "attendance.leave.approve",
            "attendance.holidays.manage", "messaging.use", "messaging.groups.create",
            "announcements.post", "announcements.manage", "notes.use", "notes.manage",
            "expenses.submit", "expenses.approve", "expenses.pay", "logistics.view",
            "logistics.edit", "logistics.dispatch", "logistics.deliver", "logistics.fleet.manage",
            "clinic.schedule.view", "clinic.appointments.manage", "clinic.patients.manage",
            "clinic.practitioner", "clinic.records.view", "clinic.providers.manage", "clinic.vitals.record"
        ])

    return {
        "roles": [{"id": r["id"], "key": r.get("key"), "name": r.get("name")} for r in roles],
        "permissions": list(permissions_set),
        "isOwner": is_owner
    }

def check_access_problem(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not row.get("tenant_id"):
        return {"status": 403, "message": "Your account isn't part of a workspace. Contact your administrator.", "code": "NO_TENANT"}
    if row.get("tenant_deleted_at"):
        return {"status": 403, "message": "This workspace has been deleted. Contact your provider.", "code": "TENANT_DELETED"}
    if not row.get("is_active", True):
        return {"status": 403, "message": "Your account has been deactivated. Contact your workspace administrator.", "code": "ACCOUNT_DISABLED"}
    if row.get("tenant_status") != "active":
        return {"status": 403, "message": "This workspace is suspended. Contact your provider.", "code": "TENANT_SUSPENDED"}
    return None

def check_session_problem(payload: Dict[str, Any], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    changed_at = row.get("password_changed_at")
    if changed_at:
        try:
            changed_ms = time.mktime(time.strptime(changed_at[:19], "%Y-%m-%dT%H:%M:%S")) * 1000
            token_iat = payload.get("iatMs") or (payload.get("iat", 0) * 1000)
            if token_iat < changed_ms:
                return {"message": "Your password was changed. Please sign in again.", "code": "SESSION_REVOKED"}
        except Exception:
            pass

    revoked_at = row.get("sessions_revoked_at")
    if revoked_at:
        try:
            revoked_ms = time.mktime(time.strptime(revoked_at[:19], "%Y-%m-%dT%H:%M:%S")) * 1000
            token_iat = payload.get("iatMs") or (payload.get("iat", 0) * 1000)
            if token_iat < revoked_ms:
                return {"message": "You were signed out by an administrator. Please sign in again.", "code": "SESSION_REVOKED"}
        except Exception:
            pass

    return None

def track_user_session(user_id: int, tenant_id: int, sid: str, ip: Optional[str] = None, user_agent: Optional[str] = None):
    try:
        now = now_iso()
        expires_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 24 * 3600))
        # Remove expired
        execute("DELETE FROM user_sessions WHERE user_id = %s AND expires_at < %s", (user_id, now))
        # Insert current
        insert_and_get_id("user_sessions", {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": sid,
            "ip_address": ip or None,
            "user_agent": (user_agent or "")[:255] or None,
            "created_at": now,
            "last_seen_at": now,
            "expires_at": expires_at
        })
    except Exception as e:
        logger.warning(f"[auth] track_user_session warning: {e}")

def start_session(
    response: Response,
    user_id: int,
    slug: str,
    settings: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[int] = None,
    is_code_login: bool = False
) -> str:
    """Signs JWT token and sets session cookies matching Node.js."""
    hours = 24
    sid = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    payload = {
        "sub": str(user_id),
        "ws": slug,
        "sid": sid,
        "isCodeLogin": bool(is_code_login),
        "iatMs": now_ms,
        "exp": int(time.time()) + (hours * 3600)
    }
    token = jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")

    max_age = hours * 3600
    is_prod = Config.ENV == "production"

    response.set_cookie(
        Config.SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="Lax",
        secure=is_prod,
        path="/"
    )
    response.set_cookie(
        workspace_cookie_name(slug),
        token,
        max_age=max_age,
        httponly=True,
        samesite="Lax",
        secure=is_prod,
        path="/"
    )

    if tenant_id:
        track_user_session(
            user_id=user_id,
            tenant_id=tenant_id,
            sid=sid,
            ip=request.headers.get("X-Forwarded-For") or request.remote_addr,
            user_agent=request.headers.get("User-Agent")
        )

    return token

def end_session(response: Response, slug: Optional[str] = None):
    if slug:
        response.delete_cookie(workspace_cookie_name(slug), path="/")
    response.delete_cookie(Config.SESSION_COOKIE, path="/")

def end_all_sessions(response: Response):
    for k in request.cookies.keys():
        if k.startswith(Config.WORKSPACE_COOKIE_PREFIX):
            response.delete_cookie(k, path="/")
    response.delete_cookie(Config.SESSION_COOKIE, path="/")

def require_auth(fn):
    """Middleware decorator enforcing authenticated session."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        slug = requested_workspace()
        token_info = get_token_from_request(slug)
        if not token_info:
            raise HttpError(401, "Please sign in.", code="UNAUTHENTICATED")

        token, cookie_name = token_info
        payload = verify_session_token(token)

        user_id = int(payload.get("sub", 0))
        row = find_user_with_tenant("u.id = %s", (user_id,))
        if not row:
            raise HttpError(401, "Please sign in.", code="UNAUTHENTICATED")

        # Workspace matching
        if payload.get("ws") and payload.get("ws") != row.get("tenant_slug"):
            raise HttpError(401, "Sign in to this workspace to continue.", code="UNAUTHENTICATED")
        if slug and row.get("tenant_slug") != slug:
            raise HttpError(401, "Sign in to this workspace to continue.", code="UNAUTHENTICATED")

        problem = check_access_problem(row) or check_session_problem(payload, row)
        if problem:
            raise HttpError(problem.get("status", 401), problem["message"], code=problem["code"])

        settings = safe_json_loads(row.get("tenant_settings"))
        features = parse_features(row.get("tenant_features"))
        access = load_access(user_id, features)

        # Attach to flask global context
        g.user = {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "role": row.get("role", "member"),
            "job_title": row.get("job_title"),
            "tenant_id": row["tenant_id"],
            "must_change_password": bool(row.get("must_change_password")),
            "is_code_login": bool(payload.get("isCodeLogin")),
            "date_of_birth": row.get("date_of_birth"),
            "joined_on": row.get("joined_on"),
            "show_birthday": row.get("show_birthday", True),
            "preferences": safe_json_loads(row.get("preferences")),
            "created_at": row.get("created_at")
        }
        g.tenant = {
            "id": row["tenant_id"],
            "name": row.get("tenant_name"),
            "slug": row.get("tenant_slug"),
            "features": features,
            "settings": settings
        }
        g.access = access
        g.maintenance = {"active": False, "upcoming": None}

        if g.user["must_change_password"] and not request.path.startswith("/api/auth"):
            raise HttpError(403, "Choose a new password to continue.", code="PASSWORD_CHANGE_REQUIRED")

        return fn(*args, **kwargs)

    return wrapper

def require_permission(*permissions: str):
    """Enforces that current user holds at least one of the permissions."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user_perms = g.access.get("permissions", [])
            if not any(p in user_perms for p in permissions):
                raise HttpError(403, "You don't have permission to do that. Ask your workspace admin for access.", code="FORBIDDEN")
            return fn(*args, **kwargs)
        return wrapper
    return decorator

def require_feature(*features: str):
    """Enforces that tenant is licensed for at least one of the features."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            tenant_features = g.tenant.get("features", [])
            if not any(f in tenant_features for f in features):
                raise HttpError(403, "This module isn't enabled for your workspace.", code="FEATURE_DISABLED")
            return fn(*args, **kwargs)
        return wrapper
    return decorator

def get_current_user() -> Dict[str, Any]:
    return getattr(g, "user", {})

def get_current_tenant() -> Dict[str, Any]:
    return getattr(g, "tenant", {})
