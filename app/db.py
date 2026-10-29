"""Data layer (SQLite). Constraints live in the schema as a second line of defence."""
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id      TEXT PRIMARY KEY CHECK (length(patient_id) = 36),
    first_name      TEXT NOT NULL CHECK (length(first_name) BETWEEN 1 AND 50),
    last_name       TEXT NOT NULL CHECK (length(last_name) BETWEEN 1 AND 50),
    date_of_birth   TEXT NOT NULL CHECK (date_of_birth GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    sex             TEXT NOT NULL CHECK (sex IN ('Male','Female','Other','Decline to Answer')),
    phone_number    TEXT NOT NULL CHECK (length(phone_number) = 10 AND phone_number NOT GLOB '*[^0-9]*'),
    email           TEXT,
    address_line_1  TEXT NOT NULL,
    address_line_2  TEXT,
    city            TEXT NOT NULL CHECK (length(city) BETWEEN 1 AND 100),
    state           TEXT NOT NULL CHECK (length(state) = 2),
    zip_code        TEXT NOT NULL CHECK (zip_code GLOB '[0-9][0-9][0-9][0-9][0-9]' OR zip_code GLOB '[0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    insurance_provider      TEXT,
    insurance_member_id     TEXT,
    preferred_language      TEXT NOT NULL DEFAULT 'English',
    emergency_contact_name  TEXT,
    emergency_contact_phone TEXT CHECK (emergency_contact_phone IS NULL OR length(emergency_contact_phone) = 10),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone_number);
CREATE INDEX IF NOT EXISTS idx_patients_last  ON patients(last_name COLLATE NOCASE);

-- Bonus: transcript/summary per call, linked to the patient it produced.
CREATE TABLE IF NOT EXISTS calls (
    call_id      TEXT PRIMARY KEY,
    patient_id   TEXT REFERENCES patients(patient_id),
    caller_number TEXT,
    summary      TEXT,
    transcript   TEXT,
    ended_reason TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

SEED = [
    dict(first_name="Jane", last_name="Doe", date_of_birth="1985-03-14", sex="Female",
         phone_number="4045550101", email="jane.doe@example.com", address_line_1="123 Peachtree St NE",
         city="Atlanta", state="GA", zip_code="30303", preferred_language="English"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> str:
    return os.environ.get("DATABASE_PATH", "data/patients.db")


@contextmanager
def conn():
    os.makedirs(os.path.dirname(db_path()) or ".", exist_ok=True)
    c = sqlite3.connect(db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db(seed: bool = True) -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        if seed and c.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0:
            for s in SEED:
                _insert(c, s)


def _to_row(data: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in data.items()}


def to_api(row: sqlite3.Row) -> dict:
    d = dict(row)
    y, m, day = d["date_of_birth"].split("-")
    d["date_of_birth"] = f"{m}/{day}/{y}"  # spec: MM/DD/YYYY
    return d


def _insert(c, data: dict) -> str:
    row = _to_row(data)
    row.setdefault("preferred_language", "English")
    row.update(patient_id=str(uuid.uuid4()), created_at=now(), updated_at=now())
    cols = ",".join(row)
    c.execute(f"INSERT INTO patients ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    return row["patient_id"]


def create_patient(data: dict) -> dict:
    with conn() as c:
        pid = _insert(c, data)
        return to_api(c.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone())


def get_patient(pid: str) -> Optional[dict]:
    with conn() as c:
        r = c.execute("SELECT * FROM patients WHERE patient_id=? AND deleted_at IS NULL", (pid,)).fetchone()
        return to_api(r) if r else None


def list_patients(last_name=None, date_of_birth: Optional[date] = None, phone_number=None,
                  limit: int = 100, offset: int = 0) -> list[dict]:
    q, args = "SELECT * FROM patients WHERE deleted_at IS NULL", []
    if last_name:
        q += " AND last_name = ? COLLATE NOCASE"; args.append(last_name)
    if date_of_birth:
        q += " AND date_of_birth = ?"; args.append(date_of_birth.isoformat())
    if phone_number:
        q += " AND phone_number = ?"; args.append(phone_number)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"; args += [limit, offset]
    with conn() as c:
        return [to_api(r) for r in c.execute(q, args)]


def update_patient(pid: str, fields: dict) -> Optional[dict]:
    row = _to_row(fields)
    with conn() as c:
        if not c.execute("SELECT 1 FROM patients WHERE patient_id=? AND deleted_at IS NULL", (pid,)).fetchone():
            return None
        if row:
            row["updated_at"] = now()
            sets = ",".join(f"{k}=?" for k in row)
            c.execute(f"UPDATE patients SET {sets} WHERE patient_id=?", [*row.values(), pid])
        return to_api(c.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone())


def soft_delete(pid: str) -> bool:
    with conn() as c:
        cur = c.execute(
            "UPDATE patients SET deleted_at=?, updated_at=? WHERE patient_id=? AND deleted_at IS NULL",
            (now(), now(), pid))
        return cur.rowcount > 0


def find_by_phone(phone: str) -> Optional[dict]:
    rows = list_patients(phone_number=phone, limit=1)
    return rows[0] if rows else None


def upsert_call(call_id: str, **fields) -> None:
    fields = {k: v for k, v in fields.items() if v is not None}
    with conn() as c:
        if c.execute("SELECT 1 FROM calls WHERE call_id=?", (call_id,)).fetchone():
            sets = ",".join(f"{k}=?" for k in [*fields, "updated_at"])
            c.execute(f"UPDATE calls SET {sets} WHERE call_id=?", [*fields.values(), now(), call_id])
        else:
            cols = ["call_id", *fields, "created_at", "updated_at"]
            vals = [call_id, *fields.values(), now(), now()]
            c.execute(f"INSERT INTO calls ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
