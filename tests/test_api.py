import os
import tempfile

import pytest
from fastapi.testclient import TestClient

GOOD = dict(first_name="Ann-Marie", last_name="O'Neil", date_of_birth="03/14/1990", sex="female",
            phone_number="(404) 555-0123", address_line_1="1 Main St", city="Atlanta", state="ga",
            zip_code="30303")


@pytest.fixture()
def client():
    d = tempfile.mkdtemp()
    os.environ["DATABASE_PATH"] = os.path.join(d, "t.db")
    os.environ.pop("VAPI_WEBHOOK_SECRET", None)
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_create_get_list_update_delete(client):
    r = client.post("/patients", json=GOOD)
    assert r.status_code == 201
    p = r.json()["data"]
    assert p["phone_number"] == "4045550123" and p["state"] == "GA" and p["sex"] == "Female"
    assert p["date_of_birth"] == "03/14/1990"
    pid = p["patient_id"]
    assert client.get(f"/patients/{pid}").json()["data"]["last_name"] == "O'Neil"
    assert len(client.get("/patients?last_name=o'neil").json()["data"]) == 1
    assert len(client.get("/patients?phone_number=404-555-0123").json()["data"]) == 1
    r = client.put(f"/patients/{pid}", json={"city": "Decatur"})
    assert r.status_code == 200 and r.json()["data"]["city"] == "Decatur"
    assert client.delete(f"/patients/{pid}").status_code == 200
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.delete(f"/patients/{pid}").status_code == 404


@pytest.mark.parametrize("field,value", [
    ("phone_number", "555123"), ("date_of_birth", "01/01/2999"), ("date_of_birth", "13/45/1990"),
    ("state", "ZZ"), ("zip_code", "1234"), ("first_name", "R2D2"), ("sex", "banana"),
])
def test_validation_422(client, field, value):
    r = client.post("/patients", json={**GOOD, field: value})
    assert r.status_code == 422
    body = r.json()
    assert body["data"] is None and body["error"]["details"][0]["field"] == field


def test_bad_json_400_and_missing_404(client):
    assert client.post("/patients", content="{nope", headers={"content-type": "application/json"}).status_code == 400
    assert client.get("/patients/00000000-0000-0000-0000-000000000000").status_code == 404


def test_voice_tool_flow_and_duplicate(client):
    def call(name, args):
        r = client.post("/vapi/webhook", json={"message": {"type": "tool-calls", "call": {"id": "c1"},
                        "toolCallList": [{"id": "t1", "name": name, "arguments": args}]}})
        return r.json()["results"][0]["result"]
    assert call("lookup_patient", {"phone_number": "4045550123"}).startswith("NO_MATCH")
    assert call("save_patient", {**GOOD, "phone_number": "12345"}).startswith("INVALID")
    assert call("save_patient", GOOD).startswith("SAVED")
    assert call("lookup_patient", {"phone_number": "4045550123"}).startswith("MATCH")
    # second "call" still sees the data (persistence across calls)
    assert len(client.get("/patients?phone_number=4045550123").json()["data"]) == 1
    client.post("/vapi/webhook", json={"message": {"type": "end-of-call-report", "call": {"id": "c1"},
                "transcript": "hi", "summary": "s", "endedReason": "hangup"}})
