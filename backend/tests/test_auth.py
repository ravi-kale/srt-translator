import pytest
from fastapi.testclient import TestClient

from main import create_app
from subtrans import auth
from subtrans.pipeline.orchestrator import JobManager

from .conftest import FakeFactory

PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    monkeypatch.setattr(auth, "FAIL_DELAY", 0)
    app = create_app(manager_factory=lambda store: JobManager(store, FakeFactory()))
    app.state.db_path = str(tmp_path / "auth.db")
    with TestClient(app) as c:
        yield c


def test_api_requires_login(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/auth/session").json() == {"auth_required": True, "configured": True, "authenticated": False}


def test_wrong_password_is_rejected_and_right_one_signs_in(client):
    r = client.post("/api/auth/login", json={"password": "nope"})
    assert r.status_code == 401 and auth.COOKIE_NAME not in r.cookies
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/jobs").status_code == 200
    assert client.get("/api/auth/session").json()["authenticated"] is True
    client.post("/api/auth/logout")
    assert client.get("/api/jobs").status_code == 401


def test_tampered_or_expired_session_is_rejected(client):
    client.post("/api/auth/login", json={"password": PASSWORD})
    token = client.cookies[auth.COOKIE_NAME]
    expires, nonce, sig = token.split(".")
    client.cookies.set(auth.COOKIE_NAME, f"{int(expires) + 999999}.{nonce}.{sig}")
    assert client.get("/api/jobs").status_code == 401
    settings = auth.Settings(_env_file=None, app_password=PASSWORD)
    old = auth.make_session(settings, now=0)
    assert not auth.session_valid(settings, old)
    other = auth.Settings(_env_file=None, app_password="a different password")
    assert not auth.session_valid(other, auth.make_session(settings))


def test_repeated_failures_are_throttled(client):
    for _ in range(auth.MAX_FAILS_PER_CLIENT):
        assert client.post("/api/auth/login", json={"password": "guess"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429


def test_vercel_without_password_refuses_api(tmp_path, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SUBTRANS_RUNNER", "local")
    app = create_app(manager_factory=lambda store: JobManager(store, FakeFactory()))
    app.state.db_path = str(tmp_path / "v.db")
    with TestClient(app) as c:
        assert c.get("/api/jobs").status_code == 503
        assert c.post("/api/auth/login", json={"password": "x"}).status_code == 503
