import datetime
from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

attendance_bp = Blueprint("attendance", __name__, url_prefix="/api/attendance")
holidays_bp = Blueprint("holidays", __name__, url_prefix="/api/holidays")

def get_today_date_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

# ==========================================
# ATTENDANCE
# ==========================================

@attendance_bp.route("/today", methods=["GET"])
@require_auth
@require_feature("attendance")
@require_permission("attendance.self")
def get_today_attendance():
    user_id = g.user["id"]
    today_str = request.args.get("date") or get_today_date_str()

    record = fetch_one("""
        SELECT * FROM attendance_records
        WHERE user_id = %s AND work_date = %s
        LIMIT 1;
    """, (user_id, today_str))

    return json_response({
        "date": today_str,
        "record": {
            "id": record["id"],
            "checkIn": record.get("check_in_local") or record.get("check_in_at"),
            "checkOut": record.get("check_out_local") or record.get("check_out_at"),
            "status": record.get("status", "present"),
            "workedHours": float(record.get("worked_hours") or 0)
        } if record else None
    })

@attendance_bp.route("/check-in", methods=["POST"])
@require_auth
@require_feature("attendance")
@require_permission("attendance.self")
def check_in():
    data = request.get_json(silent=True) or {}
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]
    today_str = data.get("date") or get_today_date_str()
    time_str = data.get("time") or datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M")
    now = now_iso()

    existing = fetch_one("""
        SELECT * FROM attendance_records
        WHERE user_id = %s AND work_date = %s;
    """, (user_id, today_str))

    if existing and existing.get("check_in_at"):
        raise HttpError(409, "You have already checked in for today.", code="ALREADY_CHECKED_IN")

    if existing:
        execute("""
            UPDATE attendance_records
            SET check_in_at = %s, check_in_local = %s, status = 'present', updated_at = %s
            WHERE id = %s;
        """, (now, time_str, now, existing["id"]))
        rec_id = existing["id"]
    else:
        rec_id = insert_and_get_id("attendance_records", {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "work_date": today_str,
            "check_in_at": now,
            "check_in_local": time_str,
            "status": "present",
            "created_at": now,
            "updated_at": now
        })

    return json_response({
        "ok": True,
        "id": rec_id,
        "date": today_str,
        "checkIn": time_str
    })

@attendance_bp.route("/check-out", methods=["POST"])
@require_auth
@require_feature("attendance")
@require_permission("attendance.self")
def check_out():
    data = request.get_json(silent=True) or {}
    user_id = g.user["id"]
    today_str = data.get("date") or get_today_date_str()
    time_str = data.get("time") or datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M")
    now = now_iso()

    existing = fetch_one("""
        SELECT * FROM attendance_records
        WHERE user_id = %s AND work_date = %s;
    """, (user_id, today_str))

    if not existing or not existing.get("check_in_at"):
        raise HttpError(400, "You must check in before checking out.", code="CHECK_IN_REQUIRED")

    execute("""
        UPDATE attendance_records
        SET check_out_at = %s, check_out_local = %s, updated_at = %s
        WHERE id = %s;
    """, (now, time_str, now, existing["id"]))

    return json_response({
        "ok": True,
        "id": existing["id"],
        "date": today_str,
        "checkOut": time_str
    })

@attendance_bp.route("/my-leaves", methods=["GET"])
@require_auth
@require_feature("attendance")
@require_permission("attendance.self")
def get_my_leaves():
    user_id = g.user["id"]
    leaves = fetch_all("""
        SELECT * FROM leave_requests
        WHERE user_id = %s
        ORDER BY id DESC;
    """, (user_id,))
    return json_response([
        {
            "id": l["id"],
            "leaveType": l.get("type", "annual"),
            "startDate": l.get("start_date"),
            "endDate": l.get("end_date"),
            "days": float(l.get("days", 1)),
            "reason": l.get("reason"),
            "status": l.get("status", "pending"),
            "createdAt": l.get("created_at")
        }
        for l in leaves
    ])

@attendance_bp.route("/request-leave", methods=["POST"])
@require_auth
@require_feature("attendance")
@require_permission("attendance.self")
def request_leave():
    data = request.get_json(silent=True) or {}
    user_id = g.user["id"]
    tenant_id = g.user["tenant_id"]
    start_date = data.get("startDate")
    end_date = data.get("endDate") or start_date
    leave_type = data.get("leaveType", "annual")
    reason = data.get("reason", "")

    if not start_date:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"startDate": "Start date is required."})

    now = now_iso()
    lid = insert_and_get_id("leave_requests", {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "type": leave_type,
        "start_date": start_date,
        "end_date": end_date,
        "days": float(data.get("days", 1)),
        "reason": reason,
        "status": "pending",
        "created_at": now,
        "updated_at": now
    })

    return json_response({
        "id": lid,
        "leaveType": leave_type,
        "startDate": start_date,
        "endDate": end_date,
        "status": "pending",
        "createdAt": now
    }, status=201)

# ==========================================
# HOLIDAYS
# ==========================================

@holidays_bp.route("", methods=["GET"])
@require_auth
@require_feature("attendance")
def get_holidays():
    tenant_id = g.user["tenant_id"]
    holidays = fetch_all("SELECT * FROM holidays WHERE tenant_id = %s ORDER BY date ASC;", (tenant_id,))
    return json_response([
        {
            "id": h["id"],
            "name": h["name"],
            "date": h["date"],
            "isOptional": bool(h.get("is_optional", False))
        }
        for h in holidays
    ])

@holidays_bp.route("", methods=["POST"])
@require_auth
@require_permission("attendance.holidays.manage")
def create_holiday():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    date_val = data.get("date")

    if not name or not date_val:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Name and date are required."})

    hid = insert_and_get_id("holidays", {
        "tenant_id": g.user["tenant_id"],
        "name": name,
        "date": date_val,
        "is_optional": bool(data.get("isOptional", False)),
        "created_at": now_iso()
    })
    return json_response({"id": hid, "name": name, "date": date_val}, status=201)

@holidays_bp.route("/<holiday_id>", methods=["DELETE"])
@require_auth
@require_permission("attendance.holidays.manage")
def delete_holiday(holiday_id):
    hid = parse_id(holiday_id)
    execute("DELETE FROM holidays WHERE id = %s AND tenant_id = %s;", (hid, g.user["tenant_id"]))
    return json_response({}, status=204)
