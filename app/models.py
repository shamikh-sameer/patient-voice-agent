"""Pydantic models = the single source of truth for validation.

Both the REST API and the voice-agent tool endpoint go through these, so the
server never trusts the LLM to have validated anything.
"""
import re
from datetime import date, datetime
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, model_validator

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP".split()
)
SEXES = ("Male", "Female", "Other", "Decline to Answer")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'\- ]*$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,40}$")


def clean_text(v: str) -> str:
    """Basic sanitisation: drop control chars and angle brackets, collapse whitespace."""
    v = re.sub(r"[\x00-\x1f\x7f<>]", "", v)
    return re.sub(r"\s+", " ", v).strip()


def normalize_phone(v: str) -> str:
    digits = re.sub(r"\D", "", v)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def v_name(v: str) -> str:
    v = clean_text(v)
    if not 1 <= len(v) <= 50:
        raise ValueError("Name must be 1-50 characters")
    if not NAME_RE.match(v):
        raise ValueError("Name may only contain letters, hyphens and apostrophes")
    return v


def v_dob(v) -> date:
    if isinstance(v, date):
        d = v
    else:
        s = clean_text(str(v))
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError("Date of birth must be a real date in MM/DD/YYYY format")
    if d > date.today():
        raise ValueError("Date of birth cannot be in the future")
    if d.year < 1900:
        raise ValueError("Date of birth year must be 1900 or later")
    return d


def v_sex(v: str) -> str:
    m = {s.lower(): s for s in SEXES}
    key = clean_text(v).lower()
    if key not in m:
        raise ValueError("Sex must be one of: Male, Female, Other, Decline to Answer")
    return m[key]


def v_phone(v: str) -> str:
    d = normalize_phone(str(v))
    if len(d) != 10 or d[0] in "01":
        raise ValueError("Phone number must be a valid 10-digit U.S. number")
    return d


def v_email(v: str) -> str:
    v = clean_text(v).lower()
    if len(v) > 254 or not EMAIL_RE.match(v):
        raise ValueError("Email format looks invalid")
    return v


def v_text(max_len: int):
    def _v(v: str) -> str:
        v = clean_text(v)
        if not 1 <= len(v) <= max_len:
            raise ValueError(f"Must be 1-{max_len} characters")
        return v
    return _v


def v_state(v: str) -> str:
    v = clean_text(v).upper()
    if v not in US_STATES:
        raise ValueError("State must be a valid 2-letter U.S. state abbreviation")
    return v


def v_zip(v: str) -> str:
    v = clean_text(v)
    if not ZIP_RE.match(v):
        raise ValueError("ZIP code must be 5 digits or ZIP+4")
    return v


def v_member_id(v: str) -> str:
    v = re.sub(r"[\s]", "", clean_text(v))
    if not MEMBER_ID_RE.match(v):
        raise ValueError("Member ID must be alphanumeric")
    return v


Name = Annotated[str, AfterValidator(v_name)]
DOB = Annotated[date, BeforeValidator(v_dob)]
Sex = Annotated[str, AfterValidator(v_sex)]
Phone = Annotated[str, AfterValidator(v_phone)]
Email = Annotated[str, AfterValidator(v_email)]
Street = Annotated[str, AfterValidator(v_text(120))]
Unit = Annotated[str, AfterValidator(v_text(50))]
City = Annotated[str, AfterValidator(v_text(100))]
State = Annotated[str, AfterValidator(v_state)]
Zip = Annotated[str, AfterValidator(v_zip)]
Org = Annotated[str, AfterValidator(v_text(100))]
MemberId = Annotated[str, AfterValidator(v_member_id)]
Language = Annotated[str, AfterValidator(v_text(50))]
FullName = Annotated[str, AfterValidator(v_text(100))]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_blanks(cls, data):
        # Voice LLMs often send "" / null for "not provided"; treat as absent.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v not in (None, "", "null", "N/A")}
        return data


class PatientCreate(_Strict):
    first_name: Name
    last_name: Name
    date_of_birth: DOB
    sex: Sex
    phone_number: Phone
    email: Optional[Email] = None
    address_line_1: Street
    address_line_2: Optional[Unit] = None
    city: City
    state: State
    zip_code: Zip
    insurance_provider: Optional[Org] = None
    insurance_member_id: Optional[MemberId] = None
    preferred_language: Language = "English"
    emergency_contact_name: Optional[FullName] = None
    emergency_contact_phone: Optional[Phone] = None


class PatientUpdate(_Strict):
    """Same fields, all optional -> partial updates."""
    first_name: Optional[Name] = None
    last_name: Optional[Name] = None
    date_of_birth: Optional[DOB] = None
    sex: Optional[Sex] = None
    phone_number: Optional[Phone] = None
    email: Optional[Email] = None
    address_line_1: Optional[Street] = None
    address_line_2: Optional[Unit] = None
    city: Optional[City] = None
    state: Optional[State] = None
    zip_code: Optional[Zip] = None
    insurance_provider: Optional[Org] = None
    insurance_member_id: Optional[MemberId] = None
    preferred_language: Optional[Language] = None
    emergency_contact_name: Optional[FullName] = None
    emergency_contact_phone: Optional[Phone] = None


PATIENT_FIELDS = list(PatientCreate.model_fields.keys())
