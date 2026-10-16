import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from main import create_app
from subtrans.pipeline.orchestrator import JobManager
from subtrans.store import JobStore

from .conftest import _PG_URL, FakeFactory, make_srt


@pytest.fixture
def fake_factory():
    return FakeFactory()


@pytest.fixture(params=["sqlite", "postgres"] if _PG_URL else ["sqlite"])
def client(request, tmp_path, fake_factory):
    app = create_app(manager_factory=lambda store: JobManager(store, fake_factory, watch_interval=0.02))
    if request.param == "postgres":
        asyncio.run(_reset_pg())
        app.state.db_path = _PG_URL
    else:
        app.state.db_path = str(tmp_path / "api.db")
    with TestClient(app) as c:
        yield c


async def _reset_pg() -> None:
    s = JobStore(_PG_URL)
    await s.db.connect()  # type: ignore[attr-defined]
    await s.db.executescript("DROP TABLE IF EXISTS blocks, jobs, job_logs, login_attempts CASCADE")  # type: ignore[attr-defined]
    await s.db.close()


def _upload(client, n=50, lang="Spanish"):
    files = {"file": ("Episode 1.srt", make_srt(n).encode("utf-8"), "application/x-subrip")}
    r = client.post("/api/jobs", files=files, data={"target_language": lang, "context_hints": "sitcom"})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_status(client, job_id, statuses, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError("timeout waiting for status")


def test_config_reports_key_presence_only(client):
    body = client.get("/api/config").json()
    assert set(body["keys"]) == {"deepseek", "claude", "gemini", "openrouter"}
    assert all(isinstance(v, bool) for v in body["keys"].values())
    assert body["defaults"]["target_size"] == 30 and body["defaults"]["max_size"] == 42


def test_upload_validation(client):
    r = client.post("/api/jobs", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/jobs", files={"file": ("empty.srt", b"nothing here", "application/x-subrip")})
    assert r.status_code == 400


def test_full_flow_run_edit_revert_export(client, fake_factory):
    fake_factory.clients[3].fix_ids = {"2"}
    detail = _upload(client)
    job_id = detail["job"]["id"]
    assert detail["job"]["block_count"] == 50 and detail["blocks"][0]["timestamp"].startswith("00:00:00,000")

    est = client.post(f"/api/jobs/{job_id}/estimate", json={"config": {}}).json()
    assert est["lines"] == 50

    r = client.post(f"/api/jobs/{job_id}/run", json={"config": {"lanes": 2}})
    assert r.status_code == 200, r.text
    _wait_status(client, job_id, {"completed"})

    blocks = client.get(f"/api/jobs/{job_id}").json()["blocks"]
    assert blocks[1]["qa_fixed"] and blocks[1]["qa_reason"] == "Fixed #2"

    r = client.patch(f"/api/jobs/{job_id}/blocks/5", json={"text": "Texto editado a mano"})
    assert r.status_code == 200 and r.json()["user_edited"] and r.json()["cps"] == 10.0

    r = client.post(f"/api/jobs/{job_id}/blocks/2/revert")
    assert r.json()["final_translation"] == r.json()["draft_translation"]

    r = client.get(f"/api/jobs/{job_id}/export")
    assert r.status_code == 200
    assert "Episode%201.spanish.srt" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    original = make_srt(50)
    exported_timings = [ln for ln in text.split("\n") if "-->" in ln]
    original_timings = [ln for ln in original.split("\n") if "-->" in ln]
    assert exported_timings == original_timings
    assert "Texto editado a mano" in text and "[ES] Line number 1." in text


def test_missing_keys_returns_400(client, fake_factory):
    fake_factory.missing = ["DEEPSEEK_API_KEY"]
    job_id = _upload(client)["job"]["id"]
    r = client.post(f"/api/jobs/{job_id}/run", json={"config": {}})
    assert r.status_code == 400
    assert r.json()["missing_keys"] == ["DEEPSEEK_API_KEY"]


def test_polling_changes_streams_every_block_status_and_logs(client, fake_factory):
    fake_factory.clients[2].delay = 0.02
    detail = _upload(client, n=120)
    job_id, rev, log_id = detail["job"]["id"], detail["rev"], 0
    assert client.post(f"/api/jobs/{job_id}/run", json={"config": {"lanes": 2}}).status_code == 200
    seen_blocks: dict[str, dict] = {}
    statuses: list[str] = []
    messages: list[str] = []
    deadline = time.time() + 20
    while time.time() < deadline:
        ch = client.get(f"/api/jobs/{job_id}/changes", params={"rev": rev, "log": log_id}).json()
        assert ch["rev"] >= rev
        rev = ch["rev"]
        if ch["logs"]:
            log_id = ch["logs"][-1]["id"]
            messages += [entry["message"] for entry in ch["logs"]]
        seen_blocks.update({b["id"]: b for b in ch["blocks"]})
        if not statuses or statuses[-1] != ch["job"]["status"]:
            statuses.append(ch["job"]["status"])
        if ch["job"]["status"] == "completed":
            break
        time.sleep(0.02)
    assert statuses[-1] == "completed"
    assert len(seen_blocks) == 120 and all(b["final_translation"] for b in seen_blocks.values())
    assert any(m.startswith("Pipeline complete") for m in messages)
    # Nothing new after the last cursor.
    ch = client.get(f"/api/jobs/{job_id}/changes", params={"rev": rev, "log": log_id}).json()
    assert ch["blocks"] == [] and ch["logs"] == []


def test_edit_shows_up_in_changes(client):
    detail = _upload(client, n=10)
    job_id, rev = detail["job"]["id"], detail["rev"]
    client.patch(f"/api/jobs/{job_id}/blocks/3", json={"text": "Hola"})
    ch = client.get(f"/api/jobs/{job_id}/changes", params={"rev": rev}).json()
    assert [b["id"] for b in ch["blocks"]] == ["3"] and ch["blocks"][0]["final_translation"] == "Hola"


def test_bible_edit_rejected_while_running_and_job_listing(client, fake_factory):
    fake_factory.clients[2].delay = 0.05
    job_id = _upload(client, n=200)["job"]["id"]
    client.post(f"/api/jobs/{job_id}/run", json={"config": {"lanes": 1}})
    _wait_status(client, job_id, {"translating"})
    r = client.put(f"/api/jobs/{job_id}/bible", json={"bible": {"synopsis_and_tone": "x"}})
    assert r.status_code == 409
    assert client.post(f"/api/jobs/{job_id}/pause").status_code == 200
    _wait_status(client, job_id, {"paused"})
    assert client.put(f"/api/jobs/{job_id}/bible", json={"bible": {"synopsis_and_tone": "x"}}).status_code == 200
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["id"] == job_id and jobs[0]["status"] == "paused"
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
