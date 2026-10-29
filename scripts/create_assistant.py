"""Create (or update) the Vapi assistant from assistant/system_prompt.md.

    python scripts/create_assistant.py            # create
    python scripts/create_assistant.py <id>       # update existing assistant

Then, in the Vapi dashboard: Phone Numbers -> buy/import a US number -> assign this assistant.
"""
import os
import sys
from pathlib import Path

import httpx

BASE = os.environ["PUBLIC_BASE_URL"].rstrip("/")
SERVER = {"url": f"{BASE}/vapi/webhook", "secret": os.environ.get("VAPI_WEBHOOK_SECRET", "")}
PROMPT = (Path(__file__).parent.parent / "assistant" / "system_prompt.md").read_text()

PHONE = {"type": "string", "description": "10-digit US phone number"}
PATIENT_PROPS = {
    "first_name": {"type": "string"}, "last_name": {"type": "string"},
    "date_of_birth": {"type": "string", "description": "MM/DD/YYYY"},
    "sex": {"type": "string", "enum": ["Male", "Female", "Other", "Decline to Answer"]},
    "phone_number": PHONE, "email": {"type": "string"},
    "address_line_1": {"type": "string"}, "address_line_2": {"type": "string"},
    "city": {"type": "string"}, "state": {"type": "string", "description": "2-letter abbreviation"},
    "zip_code": {"type": "string"}, "insurance_provider": {"type": "string"},
    "insurance_member_id": {"type": "string"}, "preferred_language": {"type": "string"},
    "emergency_contact_name": {"type": "string"}, "emergency_contact_phone": PHONE,
}


def tool(name, desc, props, required):
    return {"type": "function", "server": SERVER,
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props, "required": required}}}


config = {
    "name": "Patient Registration - Riley",
    "firstMessage": "Thanks for calling patient registration, this is Riley. Can I get your first and last name?",
    "model": {
        "provider": os.environ.get("LLM_PROVIDER", "openai"),
        "model": os.environ.get("LLM_MODEL", "gpt-4o"),
        "temperature": 0.4,
        "messages": [{"role": "system", "content": PROMPT}],
        "tools": [
            tool("lookup_patient", "Check for an existing patient by phone number. Call right after getting the phone.",
                 {"phone_number": PHONE}, ["phone_number"]),
            tool("save_patient", "Create the patient record. ONLY after the caller explicitly confirmed the read-back.",
                 PATIENT_PROPS,
                 ["first_name", "last_name", "date_of_birth", "sex", "phone_number", "address_line_1", "city", "state", "zip_code"]),
            tool("update_patient", "Update an existing patient (from lookup_patient) with only the changed fields.",
                 {"patient_id": {"type": "string"}, **PATIENT_PROPS}, ["patient_id"]),
        ],
    },
    "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "en"},
    "voice": {"provider": "vapi", "voiceId": "Elliot"},   # swap for ElevenLabs if you have a key wired in
    "endCallFunctionEnabled": True,
    "silenceTimeoutSeconds": 30,
    "maxDurationSeconds": 900,
    "backchannelingEnabled": True,
    "server": SERVER,
    "serverMessages": ["tool-calls", "end-of-call-report"],
}

headers = {"Authorization": f"Bearer {os.environ['VAPI_API_KEY']}"}
if len(sys.argv) > 1:
    r = httpx.patch(f"https://api.vapi.ai/assistant/{sys.argv[1]}", json=config, headers=headers, timeout=30)
else:
    r = httpx.post("https://api.vapi.ai/assistant", json=config, headers=headers, timeout=30)
if r.status_code >= 400:
    print("Vapi error:", r.status_code, r.text)
    sys.exit(1)
print("assistant id:", r.json()["id"])
