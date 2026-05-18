from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from joinora.models import AgentState, ParticipantName
from joinora.ws_manager import WebSocketManager
from joinora.session_store import SessionStore


class PostMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    metadata: dict[str, str] | None = None


class JoinRequest(BaseModel):
    name: ParticipantName


def create_web_app(
    store: SessionStore,
    admin_roles_path: Path | None = None,
    jwt_secret: str | None = None,
    github_client_id: str | None = None,
    github_client_secret: str | None = None,
    base_url: str | None = None,
) -> FastAPI:
    app = FastAPI()
    ws_manager = WebSocketManager()
    app.state.ws_manager = ws_manager
    app.state.store = store

    async def _notify_agent_state(session_id: str, state: AgentState) -> None:
        await ws_manager.broadcast(session_id, {"type": f"agent_{state.value}"})

    store.on_agent_state_change = _notify_agent_state

    csp = (
        "default-src 'self'; "
        "script-src 'self' cdn.jsdelivr.net; "
        "style-src 'self'; "
        "connect-src 'self' ws: wss:; "
        "img-src 'self' data:"
    )

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["Content-Security-Policy"] = csp
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

    app.add_middleware(SecurityHeadersMiddleware)

    def _authenticate(session_id: str, token: str | None) -> str | None:
        if not token:
            return None
        return store.authenticate(session_id, token)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, token: str | None = None):
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        user = _authenticate(session_id, token)
        if user is None:
            return {
                "id": session.id,
                "title": session.title,
                "status": session.status.value,
                "participant_count": len(session.participants),
            }

        participant = next((p for p in session.participants if p.name == user), None)
        return {
            "id": session.id,
            "title": session.title,
            "status": session.status.value,
            "created_at": session.created_at.isoformat(),
            "current_user": user,
            "last_seen": (
                participant.last_seen.isoformat()
                if participant and participant.last_seen
                else None
            ),
            "participants": [
                {
                    "name": p.name,
                    "last_seen": (p.last_seen.isoformat() if p.last_seen else None),
                }
                for p in session.participants
            ],
        }

    @app.get("/api/sessions/{session_id}/messages")
    def get_messages(
        session_id: str, since: str | None = None, token: str | None = None
    ):
        user = _authenticate(session_id, token)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        since_dt = None
        if since:
            since_dt = datetime.fromisoformat(since)
        try:
            return [
                m.model_dump(mode="json")
                for m in store.get_messages(session_id, since=since_dt)
            ]
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/messages", status_code=201)
    async def post_message(
        session_id: str,
        req: PostMessageRequest,
        token: str | None = None,
    ):
        user = _authenticate(session_id, token)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            message = store.add_message(
                session_id=session_id,
                author=user,
                text=req.text,
                metadata=req.metadata,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        await ws_manager.broadcast(
            session_id,
            {"type": "message_added", "message": message.model_dump(mode="json")},
        )
        return message.model_dump(mode="json")

    @app.post("/api/sessions/{session_id}/join", status_code=201)
    async def join_session(session_id: str, req: JoinRequest):
        try:
            token = store.add_participant(session_id, req.name)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail="Session not found")
            if "not active" in msg:
                raise HTTPException(status_code=410, detail="Session has ended")
            if "already taken" in msg:
                raise HTTPException(status_code=409, detail=msg)
            raise HTTPException(status_code=400, detail=msg)
        await ws_manager.broadcast(
            session_id,
            {"type": "participant_joined", "user": req.name},
        )
        return {"name": req.name, "token": token}

    @app.websocket("/ws/sessions/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str):
        token = websocket.query_params.get("token")
        user = _authenticate(session_id, token)
        if user is None:
            await websocket.close(code=4001, reason="Authentication required")
            return
        await websocket.accept()
        ws_manager.connect(session_id, websocket)
        now = datetime.now(timezone.utc)
        store.update_last_seen(session_id, user, now)
        await ws_manager.broadcast(
            session_id,
            {"type": "participant_joined", "user": user},
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(session_id, websocket)
            store.update_last_seen(session_id, user, datetime.now(timezone.utc))
            await ws_manager.broadcast(
                session_id,
                {"type": "participant_left", "user": user},
            )

    frontend_dir = Path(__file__).parent / "frontend"

    @app.get("/session/{session_id}")
    async def spa_route(session_id: str):
        index = frontend_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=404, detail="Frontend not found")

    if admin_roles_path and jwt_secret and github_client_id and github_client_secret:
        from joinora.admin_web import create_admin_app

        admin_app = create_admin_app(
            store=store,
            roles_path=admin_roles_path,
            jwt_secret=jwt_secret,
            github_client_id=github_client_id,
            github_client_secret=github_client_secret,
            base_url=base_url or "",
        )
        app.mount("/admin", admin_app)

    if frontend_dir.exists():
        app.mount(
            "/", StaticFiles(directory=str(frontend_dir), html=True), name="static"
        )

    return app
