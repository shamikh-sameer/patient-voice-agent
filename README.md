# Voice AI Patient Registration

Call a real U.S. number, talk to "Riley", and a validated patient record lands in a database
exposed through a REST API. Calling back finds the data (and recognises the returning caller).

- **Phone number:** `+1 (404) 260 2215`
- **API base URL:** `https://patient-voice-agent-production-b32f.up.railway.app` (try `GET /patients`, `GET /health`)
- **Docs (auto):** `https://patient-voice-agent-production-b32f.up.railway.app/docs`

## Architecture

```
Caller ─PSTN─▶ Vapi (telephony + STT + TTS + LLM loop)
                  │  tool calls: lookup_patient / save_patient / update_patient
                  ▼  HTTPS + X-Vapi-Secret
            FastAPI  ── /vapi/webhook ─┐
               │                       ├─▶ pydantic validation ─▶ db.py ─▶ SQLite
               └── /patients (REST) ───┘
```

| Concern | Where |
|---|---|
| Telephony / speech | Vapi (managed) |
| Conversation logic + prompt | `assistant/system_prompt.md` (commented by section), `scripts/create_assistant.py` |
| Validation (single source of truth) | `app/models.py` |
| Data layer + schema constraints | `app/db.py` |
| REST API | `app/main.py` |
| Voice-agent <-> DB glue | `app/vapi.py` |

Key design decision: the voice webhook and the REST API share the same validation models and data layer.
The LLM is never trusted to validate; invalid tool calls return `INVALID. <field>: <reason>` as the *tool
result*, which the prompt instructs the model to turn into a field-specific re-prompt.

## Tech choices
- **Vapi** - fastest path to a dialable number; lets the time go into prompt, tools and error handling.
- **FastAPI + pydantic** - validation as declarative types, auto OpenAPI docs.
- **SQLite (WAL)** - zero-ops, persists across restarts on a volume. Schema has CHECK constraints (phone length,
  sex enum, ZIP pattern, DOB format) as a second line of defence behind pydantic. Swap to Postgres by replacing `db.py`.
- **Gemini 2.5 Flash** (free tier, fast, reliable tool calling) via Vapi. Chosen because I had no OpenAI/Anthropic key
  at the time; switching is two env vars (`LLM_PROVIDER`, `LLM_MODEL`) plus re-running `create_assistant.py <id>`.
  The prompt and code are model-agnostic.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in values
# macOS/Linux: export $(grep -v '^#' .env | xargs)     Windows PowerShell: $env:NAME="value" per variable
uvicorn app.main:app --reload   # http://localhost:8000/docs
pytest -q
```
**Deploy** (Railway/Render/Fly): use the Dockerfile, mount a persistent volume at `/data`, set the env vars below.
For local dev expose with `ngrok http 8000`.

**Create the voice agent**
```bash
# macOS/Linux
export PUBLIC_BASE_URL=https://<your-host> VAPI_API_KEY=... VAPI_WEBHOOK_SECRET=... LLM_PROVIDER=google LLM_MODEL=gemini-2.5-flash
# Windows PowerShell: $env:PUBLIC_BASE_URL="https://<your-host>"  (same for the others)
python scripts/create_assistant.py     # prints assistant id
```
Then in the Vapi dashboard: Phone Numbers -> create a free US number -> assign the assistant.
Re-run with the assistant id as argument to push prompt edits.

## Environment variables
| Var | Purpose |
|---|---|
| `DATABASE_PATH` | SQLite file (use a persistent volume) |
| `VAPI_WEBHOOK_SECRET` | Shared secret; webhook rejects requests without matching `X-Vapi-Secret` |
| `PORT` | HTTP port |
| `VAPI_API_KEY`, `PUBLIC_BASE_URL`, `LLM_PROVIDER`, `LLM_MODEL` | Only for `create_assistant.py` |

No secrets are in source.

## API
`GET /patients` (`?last_name=&date_of_birth=&phone_number=&limit=&offset=`), `GET /patients/{uuid}`, `POST /patients`,
`PUT /patients/{uuid}` (partial), `DELETE /patients/{uuid}` (soft delete via `deleted_at`).
Envelope `{"data":..., "error":null}`; codes 200/201/400 (malformed JSON/query)/404/422 (field errors with details)/500.

## Edge cases handled
- Invalid DOB/phone/state/ZIP: server returns field-specific error -> agent re-asks only that field.
- DB write fails: tool returns `ERROR`; prompt forbids claiming success, offers one retry, then hands off to staff.
- Call drops mid-call: nothing is written before the explicit confirmation, so a dropped call leaves no partial record.
- Start over / corrections / interruptions / out-of-order answers: covered in the prompt.
- Duplicate detection by phone (bonus), Spanish switch (bonus, prompt-level), automated API tests (bonus).
- Call transcripts: the webhook handler and a `calls` table exist, but Vapi's end-of-call report was rejected by the secret check during testing (unresolved), so transcripts are not stored.

## Logging
`PATIENT_REGISTERED call=<id> payload=<json>` to stdout on every save; also `PATIENT_UPDATED`, `CALL_ENDED`.

## Known limitations / trade-offs
- SQLite = single instance only; Postgres is the next step for scale.
- REST API has no auth (reviewers need open access); add API keys/OAuth before real use.
- Phone is not unique (families share numbers); duplicate check offers update but allows a new record.
- Names with non-ASCII letters (e.g. "José") are rejected by the spec's alphabetic rule; relax regex for production.
- The agent discloses the name on file when a caller's phone matches (required by the bonus spec); a real system would verify identity first.
- Logs contain patient data - acceptable for test data only (not HIPAA-ready).
- LLM invention guard: the model once filled missing required fields with "Unknown"; fixed server-side (placeholders are rejected) and in the prompt.
- Vapi's Deepgram STT can mis-hear spelled names/emails; mitigated by spell-and-read-back in the prompt.

## Next steps
Appointment scheduling tool, dashboard UI, Postgres migration, call-recording retention policy, prompt regression tests with simulated callers.
