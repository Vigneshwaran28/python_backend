import re
import json
import decimal
import datetime
from typing import Any, Dict, Optional
from flask import jsonify, Response
import bcrypt

class HttpError(Exception):
    """
    Standard HTTP Error matching Node.js HttpError format.
    Produces { "message": "...", "code": "...", "errors": {...} }
    """
    def __init__(
        self,
        status: int,
        message: str,
        code: Optional[str] = None,
        errors: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        retry_after_seconds: Optional[int] = None
    ):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code or default_code_for_status(status)
        self.errors = errors
        self.data = data or {}
        self.retry_after_seconds = retry_after_seconds

    def to_dict(self) -> Dict[str, Any]:
        payload = {**self.data, "message": self.message, "code": self.code}
        if self.errors:
            payload["errors"] = self.errors
        if self.retry_after_seconds is not None:
            payload["retryAfterSeconds"] = self.retry_after_seconds
        return payload

def default_code_for_status(status: int) -> str:
    codes = {
        400: "BAD_REQUEST",
        401: "UNAUTHENTICATED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL",
        503: "SERVICE_UNAVAILABLE"
    }
    return codes.get(status, "ERROR")

def now_iso() -> str:
    """Returns ISO-8601 UTC timestamp identical to Date#toISOString() in JS."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def parse_id(val: Any) -> int:
    """Parses numeric ID or throws 404 HttpError."""
    try:
        num = int(val)
        if num <= 0:
            raise ValueError()
        return num
    except (ValueError, TypeError):
        raise HttpError(404, "Not found.", code="NOT_FOUND")

class CustomJSONEncoder(json.JSONEncoder):
    """Encodes datetimes, decimals, and UUIDs to JSON format."""
    def default(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        return super().default(obj)

def json_response(data: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> Response:
    """Serializes data using custom encoder and returns Flask response."""
    body = json.dumps(data, cls=CustomJSONEncoder)
    resp = Response(body, status=status, mimetype="application/json")
    if headers:
        for k, v in headers.items():
            resp.headers[k] = str(v)
    return resp

def safe_json_loads(val: Any, default=None):
    if not val:
        return default if default is not None else {}
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return default if default is not None else {}

# ---- Password Hashing & Verification ----

def hash_password(password: str) -> str:
    """Hashes password with bcrypt (10 rounds) matching Node's bcryptjs."""
    salt = bcrypt.gensalt(rounds=10)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def check_password(password: str, hashed: str) -> bool:
    """Verifies plaintext password against bcrypt hash."""
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

# ---- Validation Helpers ----

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

def is_valid_email(email: str) -> bool:
    return bool(email and EMAIL_REGEX.match(email.strip()))

def validate_login_input(data: dict):
    if not data:
        raise HttpError(400, "Invalid JSON body.", code="BAD_JSON")
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    code = (data.get("code") or "").strip()

    if not code and not email:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"email": "Enter a valid email address."})
    if email and not is_valid_email(email):
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"email": "Enter a valid email address."})
    if not code and not password:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"password": "Password is required."})

    return {
        "email": email,
        "password": password,
        "code": code or None,
        "workspace": (data.get("workspace") or "").strip().lower() or None
    }
