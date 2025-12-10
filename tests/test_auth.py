import uuid

import config
from auth import reset_sessions


def test_register_and_login_flow(client):
    reset_sessions()
    username = f"user_{uuid.uuid4().hex[:8]}"
    register_resp = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "supersecret",
            "confirm_password": "supersecret",
        },
    )
    assert register_resp.status_code == 201

    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "supersecret"},
    )
    assert login_resp.status_code == 200
    assert "session_token" in login_resp.cookies

    me_resp = client.get("/api/auth/me", cookies=login_resp.cookies)
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == username


def test_registration_disabled(monkeypatch, client):
    reset_sessions()
    monkeypatch.setattr(config, "AUTH_ALLOW_REGISTRATION", False)
    resp = client.post(
        "/api/auth/register",
        json={
            "username": "blocked_user",
            "password": "supersecret",
            "confirm_password": "supersecret",
        },
    )
    assert resp.status_code == 403


def test_invalid_login(client):
    reset_sessions()
    resp = client.post(
        "/api/auth/login",
        json={"username": "ghost", "password": "wrongpass"},
    )
    assert resp.status_code == 401
