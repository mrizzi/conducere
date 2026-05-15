import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from joinora.admin_web import (
    _COOKIE_NAME,
    _JWT_ALGORITHM,
    _resolve_role,
    create_admin_app,
    load_roles,
)
from joinora.session_store import SessionStore

_TEST_SECRET = "test-secret-key-for-jwt-minimum-32b"
_TEST_GH_CLIENT_ID = "fake-client-id"
_TEST_GH_CLIENT_SECRET = "fake-client-secret"


def _make_token(
    username: str,
    role: str,
    secret: str = _TEST_SECRET,
    exp_hours: float = 24,
) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=exp_hours),
    }
    return jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


class TestLoadRoles:
    def test_load_valid_roles(self, tmp_path):
        roles_file = tmp_path / "admin_roles.json"
        roles_data = {"admin": ["alice", "bob"], "viewer": ["charlie"]}
        roles_file.write_text(json.dumps(roles_data))
        result = load_roles(roles_file)
        assert result == roles_data

    def test_load_missing_file_returns_empty(self, tmp_path):
        result = load_roles(tmp_path / "nonexistent.json")
        assert result == {"admin": [], "viewer": []}

    def test_resolve_role_admin(self):
        roles = {"admin": ["alice"], "viewer": ["bob"]}
        assert _resolve_role(roles, "alice") == "admin"

    def test_resolve_role_viewer(self):
        roles = {"admin": ["alice"], "viewer": ["bob"]}
        assert _resolve_role(roles, "bob") == "viewer"

    def test_resolve_role_unknown_user(self):
        roles = {"admin": ["alice"], "viewer": ["bob"]}
        assert _resolve_role(roles, "charlie") is None


@pytest.fixture
def store(tmp_path):
    return SessionStore(repo_path=tmp_path / "repo")


@pytest.fixture
def roles_file(tmp_path):
    path = tmp_path / "admin_roles.json"
    data = {"admin": ["admin-user"], "viewer": ["viewer-user"]}
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def admin_app(store, roles_file):
    return create_admin_app(
        store=store,
        roles_path=roles_file,
        jwt_secret=_TEST_SECRET,
        github_client_id=_TEST_GH_CLIENT_ID,
        github_client_secret=_TEST_GH_CLIENT_SECRET,
        base_url="http://localhost:8000",
    )


@pytest.fixture
def admin_client(admin_app):
    return TestClient(admin_app, follow_redirects=False)


class TestAuthMiddleware:
    def test_unauthenticated_redirects_to_login(self, admin_client):
        resp = admin_client.get("/admin/api/stats")
        assert resp.status_code == 307
        assert "/admin/login" in resp.headers["location"]

    def test_login_page_accessible_without_auth(self, admin_client):
        resp = admin_client.get("/admin/login")
        assert resp.status_code == 200

    def test_valid_cookie_passes_middleware(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/admin/api/stats")
        assert resp.status_code == 200

    def test_expired_cookie_redirects(self, admin_client):
        token = _make_token("admin-user", "admin", exp_hours=-1)
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/admin/api/stats")
        assert resp.status_code == 307
        assert "/admin/login" in resp.headers["location"]

    def test_invalid_secret_redirects(self, admin_client):
        token = _make_token("admin-user", "admin", secret="wrong-secret")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/admin/api/stats")
        assert resp.status_code == 307
        assert "/admin/login" in resp.headers["location"]

    def test_logout_clears_cookie(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post("/admin/logout")
        assert resp.status_code == 307
        assert "/admin/login" in resp.headers["location"]
        set_cookie = resp.headers.get("set-cookie", "")
        assert _COOKIE_NAME in set_cookie


class TestStatsEndpoint:
    def test_stats_returns_correct_counts(self, admin_client, store):
        s1 = store.create_session(title="Active session")
        store.add_participant(s1.id, "alice")
        store.add_participant(s1.id, "bob")
        store.add_message(s1.id, "alice", "Hello")
        store.add_message(s1.id, "bob", "Hi there")

        s2 = store.create_session(title="Completed session")
        store.add_participant(s2.id, "charlie")
        store.add_message(s2.id, "charlie", "Done")
        store.end_session(s2.id)

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/admin/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_sessions"] == 1
        assert data["completed_sessions"] == 1
        assert data["total_messages"] == 3
        assert data["total_participants"] == 3

    def test_stats_accessible_to_viewer(self, admin_client):
        token = _make_token("viewer-user", "viewer")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/admin/api/stats")
        assert resp.status_code == 200

    def test_stats_counts_unique_participants(self, admin_client, store):
        s1 = store.create_session(title="Session A")
        store.add_participant(s1.id, "alice")
        s2 = store.create_session(title="Session B")
        store.add_participant(s2.id, "alice")
        store.add_participant(s2.id, "bob")

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/admin/api/stats")
        data = resp.json()
        assert data["total_participants"] == 2
