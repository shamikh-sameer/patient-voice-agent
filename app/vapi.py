"""Vapi <-> backend glue.

Vapi owns telephony + STT + TTS + the LLM loop. When the LLM decides to call a
tool it POSTs here; we validate with the SAME pydantic models as the REST API,
hit the SAME data layer, and return a short plain-text result the LLM can speak
from. Validation failures are returned as *tool results* (not HTTP errors) so
the model can re-prompt the caller for exactly the bad field.
"""
import hmac
import json
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from . import db
from .models import PATIENT_FIELDS, PatientCreate, PatientUpdate, normalize_phone, v_phone

router = APIRouter()
log = logging.getLogger("voice")


def _fmt_errors(e: ValidationError) -> str:
    parts = [f"{'.'.join(map(str, x['loc'])) or 'input'}: {str(x['msg']).removeprefix('Value error, ')}"
             for x in e.errors()]
    return ("INVALID. " + "; ".join(parts) +
            ". Do NOT save yet. Politely tell the caller what was wrong and re-ask ONLY for the field(s) named.")


def _only_patient_fields(args: dict) -> dict:
    return {k: v for k, v in args.items() if k in PATIENT_FIELDS}


def tool_lookup_patient(args: dict, call_id: str) -> str:
    try:
        phone = v_phone(str(args.get("phone_number", "")))
    except ValueError as e:
        return f"INVALID. phone_number: {e}. Re-ask for the phone number only."
    p = db.find_by_phone(phone)
    if not p:
        return "NO_MATCH. No existing record for this phone number. Continue with new registration."
    return (f"MATCH. patient_id={p['patient_id']} name={p['first_name']} {p['last_name']}. "
            f"Ask: 'It looks like we already have a record for {p['first_name']} {p['last_name']}. "
            "Would you like to update your information instead?' If they say they are someone else "
            "sharing this number, continue as a new registration.")


def tool_save_patient(args: dict, call_id: str) -> str:
    try:
        data = PatientCreate(**_only_patient_fields(args)).model_dump(exclude_none=True)
    except ValidationError as e:
        return _fmt_errors(e)
    try:
        p = db.create_patient(data)
        if call_id:
            db.upsert_call(call_id, patient_id=p["patient_id"], caller_number=None)
    except Exception:
        log.exception("save_patient failed call=%s", call_id)
        return ("ERROR. The record could NOT be saved because of a system problem. Apologize, offer to try "
                "once more; if it fails again tell the caller our staff will follow up and end politely. "
                "NEVER say it was saved.")
    log.info("PATIENT_REGISTERED call=%s payload=%s", call_id, json.dumps(p))
    return f"SAVED. patient_id={p['patient_id']}. Tell the caller: You're all set, {p['first_name']}. Then end the call."


def tool_update_patient(args: dict, call_id: str) -> str:
    pid = args.get("patient_id")
    if not pid:
        return "ERROR. patient_id missing; call lookup_patient first."
    try:
        fields = PatientUpdate(**_only_patient_fields(args)).model_dump(exclude_none=True)
    except ValidationError as e:
        return _fmt_errors(e)
    try:
        p = db.update_patient(pid, fields)
    except Exception:
        log.exception("update_patient failed call=%s", call_id)
        return "ERROR. The update could NOT be saved because of a system problem. Apologize; do not claim success."
    if not p:
        return "ERROR. That record no longer exists. Offer to register as new."
    if call_id:
        db.upsert_call(call_id, patient_id=pid)
    log.info("PATIENT_UPDATED call=%s payload=%s", call_id, json.dumps(p))
    return f"UPDATED. Tell the caller: You're all set, {p['first_name']}. Then end the call."


TOOLS = {"lookup_patient": tool_lookup_patient, "save_patient": tool_save_patient,
         "update_patient": tool_update_patient}


def _check_secret(provided: str | None) -> None:
    secret = os.environ.get("VAPI_WEBHOOK_SECRET")
    if secret and not hmac.compare_digest(provided or "", secret):
        raise HTTPException(status_code=401, detail="bad secret")


@router.post("/vapi/webhook")
async def vapi_webhook(request: Request, x_vapi_secret: str | None = Header(default=None)):
    _check_secret(x_vapi_secret)
    body = await request.json()
    msg = body.get("message", {})
    try:
        _check_secret(x_vapi_secret)
    except HTTPException:
        log.warning("webhook rejected type=%s secret_header_present=%s",
                    msg.get("type"), x_vapi_secret is not None)
        raise
    mtype = msg.get("type")
    call = msg.get("call") or {}
    call_id = call.get("id", "")

    if mtype == "tool-calls":
        results = []
        for tc in msg.get("toolCallList") or msg.get("toolCalls") or []:
            fn = tc.get("function", {})
            name = tc.get("name") or fn.get("name")
            args = tc.get("arguments") or fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            handler = TOOLS.get(name)
            try:
                result = handler(args, call_id) if handler else f"ERROR. Unknown tool {name}"
            except Exception:
                log.exception("tool %s crashed", name)
                result = "ERROR. Internal problem. Apologize and offer to retry; do not claim success."
            results.append({"toolCallId": tc.get("id"), "result": result})
        return {"results": results}

    if mtype == "end-of-call-report" and call_id:
        # Covers dropped calls too: we keep whatever transcript exists, linked by call_id.
        db.upsert_call(call_id,
                       caller_number=(call.get("customer") or {}).get("number"),
                       summary=msg.get("summary"),
                       transcript=msg.get("transcript"),
                       ended_reason=msg.get("endedReason"))
        log.info("CALL_ENDED call=%s reason=%s", call_id, msg.get("endedReason"))
    return {}
