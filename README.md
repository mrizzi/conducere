# Joinora

Collaborative multi-user runtime for structured prompt/skill execution.

**The skill controls the agenda; participants control the content.**

Joinora lets any coding agent (Claude Code, Cursor, Windsurf, etc.) run a skill collaboratively with multiple human participants. The agent drives the session through MCP tools while participants contribute via a shared browser-based conversation thread with real-time sync.

**BYOS — Bring Your Own Skill.** Any existing skill works without modification. Joinora wraps it with interaction rules that route I/O through a shared session.

---

## How It Works

```
                    ┌──────────────────────┐
                    │     Coding Agent     │
                    │  (Claude Code, etc.) │
                    └──────────┬───────────┘
                               │ MCP Tools
                    ┌──────────▼───────────┐
                    │      Joinora       │
                    │   ┌───────────────┐  │
                    │   │  MCP Server   │  │
                    │   │   (FastMCP)   │  │
                    │   └───────┬───────┘  │
                    │           │           │
                    │   ┌───────▼───────┐  │
                    │   │ Session Store │  │
                    │   │ (memory+git)  │  │
                    │   └───────┬───────┘  │
                    │           │           │
                    │   ┌───────▼───────┐  │
                    │   │  Web Server   │  │
                    │   │   (FastAPI)   │  │
                    │   └───────┬───────┘  │
                    └───────────┼───────────┘
                      WebSocket │ + REST
               ┌────────────────┼────────────────┐
               ▼                ▼                ▼
          ┌─────────┐     ┌─────────┐      ┌─────────┐
          │  Alice   │     │   Bob   │      │  Carol  │
          │ (browser)│     │(browser)│      │(browser)│
          └─────────┘     └─────────┘      └─────────┘
```

A single process runs two interfaces sharing state:

- **MCP Server** (FastMCP, Streamable HTTP) — the agent connects here and uses 6 tools to drive the session.
- **Web UI Server** (FastAPI, daemon thread) — participants connect via browser with WebSocket for real-time sync.
- **Session Store** — in-memory cache + git persistence (pygit2). Every mutation is a git commit.

---

## MCP Spec Features

Joinora is built on [FastMCP](https://gofastmcp.com) and exercises two recent additions to the [Model Context Protocol](https://modelcontextprotocol.io) specification that are central to how it works.

### Streamable HTTP Transport

Introduced in the [2025-03-26 spec revision](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), Streamable HTTP replaces the deprecated SSE transport with a single-endpoint design. One URL handles everything: `POST` for client-to-server JSON-RPC messages, `GET` for optional server-initiated SSE streams, and `DELETE` for session teardown.

Joinora supports both `stdio` (for local agent connections) and `streamable-http` (for remote agents). Streamable HTTP matters here because Joinora is a long-lived server managing multiple concurrent sessions — the single-endpoint model works cleanly with standard HTTP infrastructure (load balancers, reverse proxies, firewalls) without the connection-management issues that plagued the old two-endpoint SSE approach.

```bash
# Local agent (stdio, default)
joinora --transport stdio

# Remote agent (streamable HTTP)
joinora --transport streamable-http
```

### MCP Tasks (Async Background Operations)

Introduced in the [2025-11-25 spec revision](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks) as an experimental feature ([SEP-1686](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1686)), Tasks upgrade MCP from synchronous tool calls to a call-now, fetch-later protocol. A task-augmented request returns immediately with a durable handle while the real work continues in the background.

This is the mechanism that makes `watch_session` possible. Without Tasks, an MCP tool call blocks the agent until it returns — fine for instant operations, but Joinora needs to wait indefinitely for human participants to respond. `watch_session` is registered as a background task (`task=True` in FastMCP), so the agent can continue other work while the task monitors for participant activity:

```python
@mcp.tool(task=True)
async def watch_session(session_id: str) -> dict:
    """Runs as a background MCP Task — waits for participant activity."""
    messages = await store.wait_for_activity(session_id, timeout=300.0)
    return {"messages": [m.to_wire() for m in messages]}
```

The agent receives the task handle immediately and gets notified when participants post messages, rather than being blocked on a synchronous call.

---

## Quick Start

### Installation

```bash
pip install -e ".[dev]"
```

Requires Python 3.12+.

### Run the Server

```bash
joinora --repo-path /path/to/data --web-port 24298
```

| Flag | Default | Description |
|------|---------|-------------|
| `--repo-path` | temp directory | Git repo for session persistence |
| `--web-host` | `localhost` | Web server bind address |
| `--web-port` | `24298` | Web server port |
| `--transport` | `stdio` | MCP transport: `stdio` or `streamable-http` |
| `--admin-roles` | none | Path to `admin_roles.json` to enable the admin UI |

### Local Test

```bash
python3 test_local.py
```

Creates a session and starts the web UI for quick experimentation.

---

## MCP Tools

The agent drives sessions through six tools:

| Tool | Type | Description |
|------|------|-------------|
| `create_session` | Instant | Create a session with named participants. Returns session URLs and per-participant authentication links. |
| `post_message` | Instant | Post an AI message to the session. Supports metadata for message typing (`question`, `proposal`, `summary`, `info`) and skill phase tagging. |
| `watch_session` | Background Task | Long-lived async task that blocks until participants post new messages. Returns the batch of new messages. |
| `get_session_status` | Instant | Check session status: who's connected, message count, last-seen timestamps. |
| `get_catchup_summary` | Instant | Retrieve messages since a given timestamp — useful for generating summaries when participants rejoin. |
| `end_session` | Instant | Mark the session complete. |

### Message Metadata

Messages carry optional metadata for richer rendering and skill semantics:

```python
metadata = {
    "type": "question",   # question | proposal | summary | info
    "section": "ideation", # current phase of the skill
    "for": "Alice"         # target participant (for directed summaries)
}
```

The web UI renders each type with distinct styling — orange borders for questions, green for proposals, purple for summaries, blue for informational messages.

---

## Session Lifecycle

```
1. Agent calls create_session("Brainstorm features", ["alice", "bob"])
   → Returns session_id + per-participant URLs with auth tokens

2. Agent shares URLs with participants
   → Participants open browser, authenticate automatically via token
   → WebSocket connects for real-time sync

3. Agent posts questions via post_message
   → Message committed to git, broadcast to all connected browsers

4. Participants respond via browser UI
   → Messages committed to git, broadcast to agent + other participants

5. Agent calls watch_session (background task)
   → Blocks until participant activity
   → Returns batch of new messages

6. Agent processes responses, continues the skill
   → Posts follow-ups, proposals, summaries

7. Agent calls end_session
   → Session marked complete, final state persisted in git
```

---

## Adapter Skill

The `/joinora` adapter skill wraps any target skill for multi-user execution. It injects interaction rules that:

- Route all user communication through Joinora MCP tools (`post_message`, `watch_session`)
- Tag messages with metadata (`type`, `section`) for structured rendering
- Process all participant messages before responding
- Synthesize multi-participant input into coherent responses
- Support `/catchup` commands for participants who join late

The adapter is a template — the target skill's content is injected at runtime via a `{target_skill_content}` placeholder.

Located in `skill/skills/joinora/SKILL.md`.

---

## Web UI

The frontend is vanilla HTML/CSS/JS with no build step:

- Dark theme conversation thread
- Real-time message sync via WebSocket (auto-reconnect)
- Markdown rendering via marked.js (XSS-safe)
- Message type styling (question, proposal, summary, info)
- Participant presence indicators
- Catchup banner for returning participants
- Enter to send, Shift+Enter for newlines
- Read-only mode for unauthenticated viewers

---

## Architecture Details

### Security

- **Token isolation**: Authentication tokens are held in-memory only — never serialized to git or API responses.
- **XSS prevention**: Frontend uses `textContent` for user data, marked.js with a safe renderer that escapes HTML, and CSP headers.
- **Path safety**: Git store validates paths to prevent directory traversal.
- **Auth gate**: Every REST and WebSocket endpoint validates tokens against the session store.

### Persistence

Every session mutation is committed to a git repository:

```
sessions/{id}/session.json    # Session metadata + participants
sessions/{id}/messages.json   # Full message history
```

The in-memory store acts as a cache; the git repo is the source of truth.

### Concurrency

- `threading.Lock` for thread-safe session mutations
- `asyncio.Event` for async notification between the web server thread and MCP task coroutines
- Deep-copy semantics on `get_session` to prevent mutation leaks

---

## Admin UI

Joinora includes an optional admin dashboard for monitoring sessions, managing session lifecycle, and controlling access. It requires GitHub OAuth for authentication.

### Setting Up GitHub OAuth

1. Go to [github.com/settings/developers](https://github.com/settings/developers) and click **New OAuth App** (or **Register a new application** under OAuth Apps).

2. Fill in the form:

   | Field | Value |
   |-------|-------|
   | **Application name** | `Joinora Admin` (or any name you prefer) |
   | **Homepage URL** | `http://localhost:24298` (your Joinora server URL) |
   | **Authorization callback URL** | `http://localhost:24298/admin/callback` |

   If deploying to a remote host, replace `localhost:24298` with your actual host and port.

3. Click **Register application**.

4. On the app page, copy the **Client ID**.

5. Click **Generate a new client secret** and copy the secret immediately — it won't be shown again.

### Configuring Joinora

1. Create an `admin_roles.json` file mapping GitHub usernames to roles:

   ```json
   {
     "admin": ["your-github-username"],
     "viewer": ["colleague-github-username"]
   }
   ```

   - **admin** — full control: end/reopen sessions, manage roles
   - **viewer** — read-only access to the dashboard and session history

2. Export the OAuth credentials as environment variables:

   ```bash
   export JOINORA_GITHUB_CLIENT_ID=your_client_id
   export JOINORA_GITHUB_CLIENT_SECRET=your_client_secret
   ```

   Optionally set a stable JWT secret so admin sessions survive server restarts:

   ```bash
   export JOINORA_ADMIN_JWT_SECRET=any-long-random-string
   ```

3. Start the server with the `--admin-roles` flag:

   ```bash
   joinora --repo-path /path/to/data --admin-roles admin_roles.json
   ```

4. Open `http://localhost:24298/admin/` in your browser and sign in with GitHub.

### Admin Dashboard Features

- **Dashboard** — active/completed session counts, total messages and participants, recent sessions list
- **Sessions** — searchable session list with status filters, session detail view with participant list, message history, session URL with copy button, and end/reopen controls
- **Settings** — role management (add/remove GitHub users), OAuth configuration status

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Format
ruff format .

# Lint
ruff check .
```

### Project Structure

```
joinora/
  models.py           # Pydantic models: Session, Message, Participant
  session_store.py    # Git-backed store with async subscriber notification
  tools.py            # MCP tool functions
  server.py           # FastMCP server, CLI entry point
  web.py              # FastAPI web app (REST + WebSocket)
  git_store.py        # pygit2 wrapper
  ws_manager.py       # WebSocket connection manager
  admin_web.py        # Admin dashboard routes + GitHub OAuth
  frontend/           # Vanilla HTML/CSS/JS conversation thread UI
  admin_frontend/     # Admin dashboard SPA (HTML/CSS/JS)
skill/
  skills/joinora/   # /joinora adapter skill (BYOS wrapper)
tests/joinora/      # Tests
```

### Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.12+ |
| MCP framework | FastMCP |
| Web framework | FastAPI |
| Git operations | pygit2 |
| Frontend | Vanilla HTML/CSS/JS |
| Markdown | marked.js |
