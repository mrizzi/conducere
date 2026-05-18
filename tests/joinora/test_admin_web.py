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
        resp = admin_client.get("/api/stats")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]

    def test_login_page_accessible_without_auth(self, admin_client):
        resp = admin_client.get("/login")
        assert resp.status_code == 200

    def test_valid_cookie_passes_middleware(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/stats")
        assert resp.status_code == 200

    def test_expired_cookie_redirects(self, admin_client):
        token = _make_token("admin-user", "admin", exp_hours=-1)
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/stats")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]

    def test_invalid_secret_redirects(self, admin_client):
        token = _make_token("admin-user", "admin", secret="wrong-secret")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/stats")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]

    def test_logout_clears_cookie(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post("/logout")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]
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
        resp = admin_client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_sessions"] == 1
        assert data["completed_sessions"] == 1
        assert data["total_messages"] == 3
        assert data["total_participants"] == 3

    def test_stats_accessible_to_viewer(self, admin_client):
        token = _make_token("viewer-user", "viewer")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/stats")
        assert resp.status_code == 200

    def test_stats_counts_unique_participants(self, admin_client, store):
        s1 = store.create_session(title="Session A")
        store.add_participant(s1.id, "alice")
        s2 = store.create_session(title="Session B")
        store.add_participant(s2.id, "alice")
        store.add_participant(s2.id, "bob")

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/stats")
        data = resp.json()
        assert data["total_participants"] == 2


class TestSessionsAPI:
    def test_list_sessions(self, admin_client, store):
        store.create_session(title="Session One")
        store.create_session(title="Session Two")

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 2
        titles = {s["title"] for s in data["sessions"]}
        assert titles == {"Session One", "Session Two"}
        for s in data["sessions"]:
            assert "id" in s
            assert "status" in s
            assert "participant_count" in s
            assert "message_count" in s
            assert "created_at" in s

    def test_list_sessions_filter_by_status(self, admin_client, store):
        store.create_session(title="Active Session")
        s2 = store.create_session(title="Completed Session")
        store.end_session(s2.id)

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/sessions?status=active")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["title"] == "Active Session"

    def test_list_sessions_search(self, admin_client, store):
        store.create_session(title="Sprint Planning")
        store.create_session(title="Retrospective")

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/sessions?q=sprint")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["title"] == "Sprint Planning"

    def test_get_session_detail(self, admin_client, store):
        s = store.create_session(title="Detail Session")
        store.add_participant(s.id, "alice")
        store.add_message(s.id, "alice", "Hello world")

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get(f"/api/sessions/{s.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == s.id
        assert data["title"] == "Detail Session"
        assert data["status"] == "active"
        assert "created_at" in data
        assert len(data["participants"]) == 1
        assert data["participants"][0]["name"] == "alice"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["author"] == "alice"
        assert data["messages"][0]["text"] == "Hello world"

    def test_get_nonexistent_session_returns_404(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/sessions/nonexistent-id")
        assert resp.status_code == 404


class TestSessionActions:
    def test_end_session(self, admin_client, store):
        s = store.create_session(title="To End")
        store.add_participant(s.id, "alice")
        store.add_message(s.id, "alice", "msg")

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post(f"/api/sessions/{s.id}/end")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"

        updated = store.get_session(s.id)
        assert updated.status.value == "complete"

    def test_reopen_session(self, admin_client, store):
        s = store.create_session(title="To Reopen")
        store.end_session(s.id)

        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post(f"/api/sessions/{s.id}/reopen")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"

        updated = store.get_session(s.id)
        assert updated.status.value == "active"

    def test_viewer_cannot_end_session(self, admin_client, store):
        s = store.create_session(title="Protected")

        token = _make_token("viewer-user", "viewer")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post(f"/api/sessions/{s.id}/end")
        assert resp.status_code == 403

    def test_viewer_cannot_reopen_session(self, admin_client, store):
        s = store.create_session(title="Protected")
        store.end_session(s.id)

        token = _make_token("viewer-user", "viewer")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post(f"/api/sessions/{s.id}/reopen")
        assert resp.status_code == 403


class TestSettingsAPI:
    def test_get_roles(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/roles")
        assert resp.status_code == 200
        data = resp.json()
        assert "admin-user" in data["admin"]
        assert "viewer-user" in data["viewer"]

    def test_update_roles(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        new_roles = {"admin": ["admin-user", "new-admin"], "viewer": ["viewer-user"]}
        resp = admin_client.put("/api/roles", json=new_roles)
        assert resp.status_code == 200
        data = resp.json()
        assert data == new_roles

        resp = admin_client.get("/api/roles")
        assert resp.status_code == 200
        assert resp.json() == new_roles

    def test_viewer_cannot_update_roles(self, admin_client):
        token = _make_token("viewer-user", "viewer")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.put(
            "/api/roles",
            json={"admin": ["viewer-user"], "viewer": []},
        )
        assert resp.status_code == 403

    def test_get_oauth_status(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/oauth-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["client_id"] == "fake***"
        assert data["callback_url"] == "http://localhost:8000/admin/callback"

    def test_viewer_cannot_see_oauth_status(self, admin_client):
        token = _make_token("viewer-user", "viewer")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/oauth-status")
        assert resp.status_code == 403

    def test_cannot_empty_admin_list(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.put(
            "/api/roles",
            json={"admin": [], "viewer": ["admin-user"]},
        )
        assert resp.status_code == 422


class TestOAuthCallback:
    def _mock_github(self, username="admin-user", access_token="gho_fake"):
        from unittest.mock import AsyncMock

        mock_client = AsyncMock()
        mock_client.post.return_value = AsyncMock(
            json=lambda: (
                {"access_token": access_token}
                if access_token
                else {"error": "bad_code"}
            )
        )
        mock_client.get.return_value = AsyncMock(json=lambda: {"login": username})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    def test_successful_login_sets_cookie_and_redirects(self, admin_client):
        from unittest.mock import patch

        state = "test-state-value"
        admin_client.cookies.set("_oauth_state", state)
        with patch(
            "joinora.admin_web.httpx.AsyncClient", return_value=self._mock_github()
        ):
            resp = admin_client.get(f"/callback?code=test-code&state={state}")
        assert resp.status_code == 307
        assert "/admin/" in resp.headers["location"]
        set_cookie = resp.headers.get("set-cookie", "")
        assert _COOKIE_NAME in set_cookie
        assert "httponly" in set_cookie.lower()

    def test_missing_state_returns_403(self, admin_client):
        from unittest.mock import patch

        admin_client.cookies.set("_oauth_state", "expected-state")
        with patch(
            "joinora.admin_web.httpx.AsyncClient", return_value=self._mock_github()
        ):
            resp = admin_client.get("/callback?code=test-code&state=wrong-state")
        assert resp.status_code == 403

    def test_missing_access_token_returns_401(self, admin_client):
        from unittest.mock import patch

        state = "test-state"
        admin_client.cookies.set("_oauth_state", state)
        with patch(
            "joinora.admin_web.httpx.AsyncClient",
            return_value=self._mock_github(access_token=None),
        ):
            resp = admin_client.get(f"/callback?code=bad-code&state={state}")
        assert resp.status_code == 401

    def test_unknown_user_returns_403(self, admin_client):
        from unittest.mock import patch

        state = "test-state"
        admin_client.cookies.set("_oauth_state", state)
        with patch(
            "joinora.admin_web.httpx.AsyncClient",
            return_value=self._mock_github(username="unknown-user"),
        ):
            resp = admin_client.get(f"/callback?code=test-code&state={state}")
        assert resp.status_code == 403


class TestSessionActionErrors:
    def test_end_nonexistent_session_returns_404(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post("/api/sessions/nonexistent-id/end")
        assert resp.status_code == 404

    def test_reopen_nonexistent_session_returns_400(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post("/api/sessions/nonexistent-id/reopen")
        assert resp.status_code == 400

    def test_reopen_active_session_returns_400(self, admin_client, store):
        s = store.create_session(title="Already Active")
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.post(f"/api/sessions/{s.id}/reopen")
        assert resp.status_code == 400


class TestLoginPage:
    def test_login_page_contains_github_oauth_url(self, admin_client):
        resp = admin_client.get("/login")
        assert resp.status_code == 200
        body = resp.text
        assert "github.com/login/oauth/authorize" in body
        assert "fake-client-id" in body
        assert "admin" in body and "callback" in body


class TestRolesAccess:
    def test_viewer_can_read_roles(self, admin_client):
        token = _make_token("viewer-user", "viewer")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/api/roles")
        assert resp.status_code == 200
        data = resp.json()
        assert "admin" in data
        assert "viewer" in data


class TestAdminFrontend:
    def test_admin_spa_serves_html(self, admin_client):
        token = _make_token("admin-user", "admin")
        admin_client.cookies.set(_COOKIE_NAME, token)
        resp = admin_client.get("/")
        assert resp.status_code == 200
        assert "Joinora Admin" in resp.text


class TestAdminIntegration:
    def test_admin_routes_mounted_on_web_app(self, store, roles_file):
        from joinora.web import create_web_app

        app = create_web_app(
            store=store,
            admin_roles_path=roles_file,
            jwt_secret="test-secret-key-for-jwt-minimum-32b",
            github_client_id="test-id",
            github_client_secret="test-secret",
            base_url="http://localhost:8000",
        )
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/admin/login")
        assert resp.status_code == 200

    def test_participant_routes_still_work(self, store, roles_file):
        from joinora.web import create_web_app

        app = create_web_app(
            store=store,
            admin_roles_path=roles_file,
            jwt_secret="test-secret-key-for-jwt-minimum-32b",
            github_client_id="test-id",
            github_client_secret="test-secret",
            base_url="http://localhost:8000",
        )
        client = TestClient(app)
        session = store.create_session(title="Test")
        resp = client.get(f"/api/sessions/{session.id}")
        assert resp.status_code == 200
