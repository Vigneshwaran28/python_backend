import random
from flask import Blueprint, request, g
from db.connection import fetch_one, fetch_all, execute, insert_and_get_id
from middleware.auth import require_auth, require_feature, require_permission
from utils.helpers import HttpError, json_response, parse_id, now_iso

clinic_bp = Blueprint("clinic", __name__, url_prefix="/api/clinic")

# ==========================================
# QUEUE
# ==========================================

@clinic_bp.route("/queue", methods=["GET"])
@require_auth
@require_feature("clinic")
def get_queue():
    tenant_id = g.user["tenant_id"]
    query = """
        SELECT a.id, a.portal_number, a.status, a.scheduled_at, a.triage_vitals,
               p.id as patient_id, p.name as patient_name, p.mrn,
               pr.name as provider_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        LEFT JOIN clinic_providers pr ON pr.id = a.provider_id
        WHERE a.tenant_id = %s AND a.status IN ('checked_in', 'in_progress', 'scheduled')
        ORDER BY a.scheduled_at ASC;
    """
    rows = fetch_all(query, (tenant_id,))
    return json_response([
        {
            "id": r["id"],
            "token": f"Q-{r['id']}",
            "token_number": f"Q-{r['id']}",
            "status": r.get("status", "checked_in"),
            "patientId": r["patient_id"],
            "patientName": r["patient_name"],
            "mrn": r.get("mrn"),
            "providerName": r.get("provider_name"),
            "scheduledAt": r.get("scheduled_at")
        }
        for r in rows
    ])

@clinic_bp.route("/queue", methods=["POST"])
@require_auth
@require_feature("clinic")
@require_permission("clinic.appointments.manage")
def add_to_queue():
    data = request.get_json(silent=True) or {}
    patient_id = parse_id(data.get("patientId") or data.get("patient_id"))
    provider_id = parse_id(data.get("providerId")) if data.get("providerId") else None
    now = now_iso()

    aid = insert_and_get_id("appointments", {
        "tenant_id": g.user["tenant_id"],
        "patient_id": patient_id,
        "provider_id": provider_id,
        "scheduled_at": now,
        "status": "checked_in",
        "created_at": now,
        "updated_at": now
    })

    return json_response({
        "id": aid,
        "token": f"Q-{aid}",
        "status": "checked_in",
        "createdAt": now
    }, status=201)

# ==========================================
# PATIENTS
# ==========================================

@clinic_bp.route("/patients", methods=["GET"])
@require_auth
@require_feature("clinic")
@require_permission("clinic.patients.manage", "clinic.records.view")
def get_patients():
    tenant_id = g.user["tenant_id"]
    search = request.args.get("search")
    query = "SELECT * FROM patients WHERE tenant_id = %s"
    params = [tenant_id]

    if search:
        query += " AND (LOWER(name) LIKE %s OR LOWER(mrn) LIKE %s OR phone LIKE %s)"
        term = f"%{search.lower()}%"
        params.extend([term, term, f"%{search}%"])

    query += " ORDER BY name ASC;"
    rows = fetch_all(query, tuple(params))
    return json_response([
        {
            "id": p["id"],
            "mrn": p.get("mrn"),
            "name": p["name"],
            "dateOfBirth": p.get("date_of_birth"),
            "gender": p.get("gender", "unknown"),
            "phone": p.get("phone"),
            "email": p.get("email"),
            "address": p.get("address"),
            "bloodGroup": p.get("blood_group"),
            "allergies": p.get("allergies"),
            "chronicConditions": p.get("chronic_conditions"),
            "createdAt": p.get("created_at")
        }
        for p in rows
    ])

@clinic_bp.route("/patients", methods=["POST"])
@require_auth
@require_feature("clinic")
@require_permission("clinic.patients.manage")
def create_patient():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"name": "Patient name is required."})

    tenant_id = g.user["tenant_id"]
    mrn = f"MRN-{random.randint(100000, 999999)}"
    now = now_iso()

    pid = insert_and_get_id("patients", {
        "tenant_id": tenant_id,
        "mrn": mrn,
        "name": name,
        "gender": data.get("gender", "unknown"),
        "date_of_birth": data.get("dateOfBirth"),
        "phone": data.get("phone"),
        "email": data.get("email"),
        "address": data.get("address"),
        "blood_group": data.get("bloodGroup"),
        "allergies": data.get("allergies"),
        "chronic_conditions": data.get("chronicConditions"),
        "created_at": now,
        "updated_at": now
    })

    return json_response({"id": pid, "mrn": mrn, "name": name}, status=201)

# ==========================================
# APPOINTMENTS
# ==========================================

@clinic_bp.route("/appointments", methods=["GET"])
@require_auth
@require_feature("clinic")
@require_permission("clinic.schedule.view")
def get_appointments():
    tenant_id = g.user["tenant_id"]
    query = """
        SELECT a.*, p.name as patient_name, p.mrn, pr.name as provider_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        LEFT JOIN clinic_providers pr ON pr.id = a.provider_id
        WHERE a.tenant_id = %s
        ORDER BY a.scheduled_at DESC;
    """
    rows = fetch_all(query, (tenant_id,))
    return json_response([
        {
            "id": a["id"],
            "patientId": a["patient_id"],
            "patientName": a["patient_name"],
            "mrn": a["mrn"],
            "providerId": a.get("provider_id"),
            "providerName": a.get("provider_name"),
            "scheduledAt": a.get("scheduled_at"),
            "status": a.get("status", "scheduled"),
            "createdAt": a.get("created_at")
        }
        for a in rows
    ])

@clinic_bp.route("/appointments", methods=["POST"])
@require_auth
@require_feature("clinic")
@require_permission("clinic.appointments.manage")
def create_appointment():
    data = request.get_json(silent=True) or {}
    patient_id = parse_id(data.get("patientId") or data.get("patient_id"))
    scheduled_at = data.get("scheduledAt")
    if not scheduled_at:
        raise HttpError(422, "Please fix the highlighted fields.", code="VALIDATION_ERROR", errors={"scheduledAt": "Date and time required."})

    now = now_iso()
    aid = insert_and_get_id("appointments", {
        "tenant_id": g.user["tenant_id"],
        "patient_id": patient_id,
        "provider_id": parse_id(data.get("providerId")) if data.get("providerId") else None,
        "scheduled_at": scheduled_at,
        "status": "scheduled",
        "created_at": now,
        "updated_at": now
    })

    return json_response({"id": aid, "status": "scheduled", "scheduledAt": scheduled_at}, status=201)
