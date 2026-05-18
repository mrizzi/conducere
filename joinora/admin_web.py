import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from joinora.models import SessionStatus
from joinora.session_store import SessionStore

_JWT_ALGORITHM = "HS256"
_COOKIE_NAME = "joinora_admin"
_JWT_EXPIRY_HOURS = 24

_PUBLIC_PATHS = {"/login", "/callback"}


def load_roles(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {"admin": [], "viewer": []}
    with open(path) as f:
        return json.load(f)


def _resolve_role(roles: dict[str, list[str]], username: str) -> str | None:
    for role, users in roles.items():
        if username in users:
            return role
    return None


def _require_admin(request: Request) -> None:
    if getattr(request.state, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


def create_admin_app(
    store: SessionStore,
    roles_path: Path,
    jwt_secret: str,
    github_client_id: str,
    github_client_secret: str,
    base_url: str,
) -> FastAPI:
    app = FastAPI()
    roles = load_roles(roles_path)

    class AdminAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            root = request.scope.get("root_path", "")
            full_path = request.url.path
            path = (
                full_path[len(root) :]
                if root and full_path.startswith(root)
                else full_path
            )
            if path in _PUBLIC_PATHS or path.startswith("/static"):
                return await call_next(request)

            token = request.cookies.get(_COOKIE_NAME)
            if token:
                try:
                    payload = jwt.decode(token, jwt_secret, algorithms=[_JWT_ALGORITHM])
                    username = payload.get("sub")
                    role = payload.get("role")
                    if username and role:
                        request.state.username = username
                        request.state.role = role
                        return await call_next(request)
                except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                    pass

            return RedirectResponse(url="/admin/login", status_code=307)

    app.add_middleware(AdminAuthMiddleware)

    @app.get("/login")
    async def login_page():
        state = secrets.token_urlsafe(32)
        params = urlencode(
            {
                "client_id": github_client_id,
                "redirect_uri": f"{base_url}/admin/callback",
                "scope": "read:user",
                "state": state,
            }
        )
        github_url = f"https://github.com/login/oauth/authorize?{params}"
        response = Response(
            content=(
                "<!DOCTYPE html>"
                '<html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                "<title>Joinora Admin - Sign In</title>"
                '<link rel="stylesheet" href="/admin/static/admin.css">'
                "</head><body>"
                '<div class="login-box">'
                "<h1>Joinora Admin</h1>"
                f'<a href="{github_url}">Sign in with GitHub</a>'
                "</div></body></html>"
            ),
            media_type="text/html",
        )
        response.set_cookie(
            key="_oauth_state",
            value=state,
            httponly=True,
            samesite="lax",
            max_age=600,
        )
        return response

    @app.get("/callback")
    async def github_callback(code: str, state: str, request: Request):
        expected_state = request.cookies.get("_oauth_state")
        if not expected_state or state != expected_state:
            raise HTTPException(status_code=403, detail="Invalid OAuth state")

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": github_client_id,
                    "client_secret": github_client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise HTTPException(status_code=401, detail="GitHub auth failed")

            user_resp = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            username = user_resp.json().get("login")

        role = _resolve_role(roles, username)
        if role is None:
            raise HTTPException(status_code=403, detail="Not authorized")

        payload = {
            "sub": username,
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRY_HOURS),
        }
        token = jwt.encode(payload, jwt_secret, algorithm=_JWT_ALGORITHM)

        response = RedirectResponse(url="/admin/", status_code=307)
        response.set_cookie(
            key=_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=_JWT_EXPIRY_HOURS * 3600,
        )
        response.delete_cookie(key="_oauth_state")
        return response

    @app.post("/logout")
    async def logout():
        response = RedirectResponse(url="/admin/login", status_code=307)
        response.delete_cookie(key=_COOKIE_NAME)
        return response

    @app.get("/api/stats")
    async def get_stats(request: Request):
        sessions = store.list_all_sessions()
        active = sum(1 for s in sessions if s.status == SessionStatus.ACTIVE)
        completed = sum(1 for s in sessions if s.status == SessionStatus.COMPLETE)
        total_messages = sum(len(s.messages) for s in sessions)
        all_participants: set[str] = set()
        for s in sessions:
            for p in s.participants:
                all_participants.add(p.name)
        return {
            "active_sessions": active,
            "completed_sessions": completed,
            "total_messages": total_messages,
            "total_participants": len(all_participants),
        }

    @app.get("/api/sessions")
    async def list_sessions(
        request: Request,
        status: str | None = None,
        q: str | None = None,
    ):
        sessions = store.list_all_sessions()
        if status:
            sessions = [s for s in sessions if s.status.value == status]
        if q:
            q_lower = q.lower()
            sessions = [s for s in sessions if q_lower in s.title.lower()]
        return {
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "status": s.status.value,
                    "participant_count": len(s.participants),
                    "message_count": len(s.messages),
                    "created_at": s.created_at.isoformat(),
                }
                for s in sessions
            ]
        }

    @app.get("/api/sessions/{session_id}")
    async def get_session_detail(session_id: str):
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "id": session.id,
            "title": session.title,
            "status": session.status.value,
            "created_at": session.created_at.isoformat(),
            "participants": [
                {
                    "name": p.name,
                    "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                }
                for p in session.participants
            ],
            "messages": [m.model_dump(mode="json") for m in session.messages],
        }

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(request: Request, session_id: str):
        _require_admin(request)
        try:
            result = store.end_session(session_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return result

    @app.post("/api/sessions/{session_id}/reopen")
    async def reopen_session(request: Request, session_id: str):
        _require_admin(request)
        try:
            store.reopen_session(session_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"status": "active"}

    @app.get("/api/roles")
    async def get_roles(request: Request):
        return roles

    @app.put("/api/roles")
    async def update_roles(request: Request):
        nonlocal roles
        _require_admin(request)
        new_roles = await request.json()
        if not isinstance(new_roles, dict):
            raise HTTPException(status_code=422, detail="Expected JSON object")
        validated = {"admin": [], "viewer": []}
        for key in validated:
            if key in new_roles:
                if not isinstance(new_roles[key], list) or not all(
                    isinstance(u, str) for u in new_roles[key]
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=f"'{key}' must be a list of strings",
                    )
                validated[key] = new_roles[key]
        if not validated["admin"]:
            raise HTTPException(status_code=422, detail="Admin list cannot be empty")
        with open(roles_path, "w") as f:
            json.dump(validated, f, indent=2)
        roles = validated
        return roles

    @app.get("/api/oauth-status")
    async def get_oauth_status(request: Request):
        _require_admin(request)
        configured = bool(github_client_id and github_client_secret)
        masked_id = github_client_id[:4] + "***" if github_client_id else ""
        return {
            "configured": configured,
            "client_id": masked_id,
            "callback_url": f"{base_url}/admin/callback",
        }

    admin_frontend_dir = Path(__file__).parent / "admin_frontend"

    @app.get("/")
    async def admin_spa():
        index = admin_frontend_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=404, detail="Admin frontend not found")

    if admin_frontend_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(admin_frontend_dir)),
            name="admin_static",
        )

    return app
