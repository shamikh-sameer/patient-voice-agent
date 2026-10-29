"""REST API (+ mounts the Vapi webhook). Run: uvicorn app.main:app"""
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import db
from .models import PatientCreate, PatientUpdate, normalize_phone, v_dob
from .vapi import router as vapi_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Patient Registration API", lifespan=lifespan)
app.include_router(vapi_router)


def envelope(data=None, error=None, status=200):
    return JSONResponse({"data": data, "error": error}, status_code=status)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    errs = exc.errors()
    if any(e["type"] == "json_invalid" for e in errs):
        return envelope(error={"code": "bad_request", "message": "Malformed JSON body"}, status=400)
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or None,
                "message": str(e["msg"]).removeprefix("Value error, ")} for e in errs]
    return envelope(error={"code": "validation_error", "message": "Invalid input", "details": details}, status=422)


@app.exception_handler(sqlite3.Error)
async def db_handler(_: Request, exc: sqlite3.Error):
    log.exception("database error")
    return envelope(error={"code": "internal_error", "message": "Database error"}, status=500)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("unhandled error")
    return envelope(error={"code": "internal_error", "message": "Internal server error"}, status=500)


def _not_found():
    return envelope(error={"code": "not_found", "message": "Patient not found"}, status=404)


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    """Read-only HTML dashboard; it renders data from GET /patients."""
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.get("/health")
def health():
    return envelope({"status": "ok"})


@app.get("/patients")
def list_patients(last_name: Optional[str] = None, date_of_birth: Optional[str] = None,
                  phone_number: Optional[str] = None,
                  limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    try:
        dob = v_dob(date_of_birth) if date_of_birth else None
    except ValueError as e:
        return envelope(error={"code": "bad_request", "message": str(e)}, status=400)
    phone = normalize_phone(phone_number) if phone_number else None
    return envelope(db.list_patients(last_name, dob, phone, limit, offset))


@app.get("/patients/{patient_id}")
def get_patient(patient_id: UUID):
    p = db.get_patient(str(patient_id))
    return envelope(p) if p else _not_found()


@app.post("/patients")
def create_patient(body: PatientCreate):
    p = db.create_patient(body.model_dump(exclude_none=True))
    log.info("PATIENT_CREATED via=api patient_id=%s", p["patient_id"])
    return envelope(p, status=201)


@app.put("/patients/{patient_id}")
def update_patient(patient_id: UUID, body: PatientUpdate):
    p = db.update_patient(str(patient_id), body.model_dump(exclude_none=True))
    return envelope(p) if p else _not_found()


@app.delete("/patients/{patient_id}")
def delete_patient(patient_id: UUID):
    return envelope({"deleted": True}) if db.soft_delete(str(patient_id)) else _not_found()
