# Admin UI Design

## Overview

A web-based admin dashboard for Joinora, embedded in the existing FastAPI process. Provides session monitoring, session lifecycle management, and role-based access control via GitHub OAuth. Vanilla HTML/CSS/JS frontend with no build step, consistent with the participant UI.

## Architecture

Admin functionality lives alongside the existing Joinora server:

- **Backend:** New admin routes under `/admin/` prefix in `joinora/admin_web.py`. Shares `SessionStore` directly — no API indirection.
- **Frontend:** Separate directory `joinora/admin_frontend/` with HTML, CSS, and JS files. Served as static files from `/admin/static/`.
- **Auth middleware:** Decorates all `/admin/` routes (except `/admin/login` and `/admin/callback`). Validates JWT session cookie, injects role into request state.
- **No WebSocket:** Admin views use manual page refresh. No real-time updates needed.

```
joinora/
  admin_web.py              # FastAPI admin routes + OAuth + middleware
  admin_frontend/
    index.html              # Admin SPA
    admin.js                # Dashboard, sessions, settings logic
    admin.css               # Admin-specific styles (reuses design tokens from participant UI)
```

## Authentication

GitHub OAuth with JWT session cookies.

### Flow

1. Unauthenticated request to `/admin/*` → redirect to `/admin/login`
2. `/admin/login` renders a page with "Sign in with GitHub" button
3. Button redirects to GitHub OAuth authorize URL with `read:user` scope
4. GitHub redirects to `/admin/callback?code=...`
5. Server exchanges code for access token, fetches GitHub username via API
6. Server checks username against `admin_roles.json` — if not listed, return 403
7. Server issues a signed JWT cookie containing `{username, role, exp}`
8. Subsequent requests validated via cookie; expired/invalid → redirect to login

### Configuration

Environment variables:
- `JOINORA_GITHUB_CLIENT_ID` — GitHub OAuth app client ID
- `JOINORA_GITHUB_CLIENT_SECRET` — GitHub OAuth app client secret
- `JOINORA_ADMIN_JWT_SECRET` — Secret for signing JWT cookies. If not set, a random secret is generated at server startup (valid for that process lifetime only — sessions won't survive restarts without setting this explicitly)

## Authorization

Two roles: **admin** and **viewer**.

### Role Configuration

File: `admin_roles.json` in the repository root (path configurable via `--admin-roles` CLI arg).

```json
{
  "admin": ["github-username-1", "github-username-2"],
  "viewer": ["github-username-3"]
}
```

### Permission Matrix

| Action                          | Admin | Viewer |
|---------------------------------|:-----:|:------:|
| View dashboard & session list   | yes   | yes    |
| View session detail & messages  | yes   | yes    |
| End session                     | yes   | no     |
| Reopen session                  | yes   | no     |
| Manage roles (settings)         | yes   | no     |

## Pages & Layout

Top navigation bar with three tabs. Username and role displayed in the nav bar.

### Dashboard Tab

Summary view of all Joinora activity.

**Stats cards (top row):**
- Active sessions count
- Completed sessions count
- Total messages across all sessions
- Total unique participants

**Recent sessions table:**
Each row shows: session title, status (active/complete), participant count, message count. Rows are clickable — navigating to the Sessions tab with that session selected.

### Sessions Tab

List + detail split layout.

**Left panel — session list:**
- Search input (filters by session title)
- Status filter tabs: All / Active / Complete
- Session entries showing: title, status dot, participant count, message count, creation time
- Selected session is highlighted

**Right panel — session detail:**

Header section:
- Session title
- Status badge (active/complete)
- Creation timestamp
- Session URL as a clickable link (opens participant view in new browser tab) with a copy-to-clipboard button
- Action buttons (admin only): End Session (for active) / Reopen Session (for complete)

Participants section:
- List of participant names
- Last-seen timestamp per participant
- Online/offline indicator (green dot if last_seen is recent, gray otherwise)

Messages section:
- Read-only scrollable list of all messages in chronological order
- Each message shows: author name (color-coded), message text (markdown rendered), timestamp
- Metadata tags displayed if present (type, section, for)
- No edit or delete actions — messages are immutable

### Settings Tab

Admin-only content (viewers see read-only role list).

**Role management table:**
- Columns: GitHub username, role (admin/viewer), actions
- Admin actions: add user (username + role dropdown), remove user, change role
- Edits modify `admin_roles.json` on disk

**OAuth configuration display (read-only):**
- GitHub OAuth client ID (masked)
- Callback URL
- Verification that OAuth is properly configured

## Backend API Routes

All under `/admin/` prefix. Protected by auth middleware unless noted.

### Auth Routes (no auth required)

| Route | Method | Purpose |
|-------|--------|---------|
| `/admin/login` | GET | Render login page |
| `/admin/callback` | GET | GitHub OAuth callback, set JWT cookie |
| `/admin/logout` | POST | Clear session cookie |

### Dashboard Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/admin/api/stats` | GET | Aggregate stats (session counts, message totals, participant totals) |
| `/admin/api/sessions` | GET | List all sessions with summary info. Query params: `status`, `q` (search) |

### Session Management Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/admin/api/sessions/{id}` | GET | Full session detail (participants, messages) |
| `/admin/api/sessions/{id}/end` | POST | End session (admin only) |
| `/admin/api/sessions/{id}/reopen` | POST | Reopen session (admin only) |

### Settings Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/admin/api/roles` | GET | Current role assignments |
| `/admin/api/roles` | PUT | Update role assignments (admin only) |
| `/admin/api/oauth-status` | GET | OAuth config status (admin only) |

## Frontend Details

### Tech Approach

Vanilla HTML/CSS/JS SPA. Single `index.html` with tab switching via JS (no page reloads between tabs). CSS uses the same dark theme design tokens as the participant UI (`#1a1a2e` background, `#e2e8f0` text, matching color palette).

### Tab Routing

Hash-based routing: `/admin/#dashboard`, `/admin/#sessions`, `/admin/#sessions/{id}`, `/admin/#settings`. JS reads the hash on load and tab switch, fetches data from the admin API, and renders the appropriate view.

### Session URL Copy Button

The session detail panel displays the participant URL (`{base_url}/session/{id}`) as a clickable `<a>` tag with `target="_blank"`. Adjacent copy button uses `navigator.clipboard.writeText()` with a brief "Copied!" confirmation.

### Role-Based UI

Viewer role: action buttons (End, Reopen, role management) are hidden via JS based on the role in the JWT payload (decoded client-side for UI purposes only — all enforcement is server-side).

## Security Considerations

- JWT cookie: `HttpOnly`, `SameSite=Lax`, `Secure` in production
- CSRF: SameSite cookie + checking `Origin` header on state-changing requests
- OAuth state parameter to prevent CSRF on the OAuth flow
- Role config file permissions should restrict write access on the host
- Admin API endpoints enforce role checks server-side regardless of frontend state

## What Is Explicitly Out of Scope

- **Participant removal** — sessions are open by design; removing a participant doesn't prevent them from re-joining, making the action meaningless
- **Message moderation** — messages are immutable; deleting them doesn't remove them from the agent's conversation context, so it would be misleading
- **Export/import** — the git repository is the persistent record; export can be added later if a real need emerges
- **Real-time WebSocket updates** — manual refresh is sufficient for admin use
- **Session creation from admin UI** — sessions are created by agents via MCP tools; admin UI manages existing sessions
- **Multiple OAuth providers** — GitHub only for now; generic OIDC can be added later

## Dependencies

New Python dependencies:
- `PyJWT` — JWT encoding/decoding for session cookies
- `httpx` — HTTP client for GitHub OAuth token exchange and API calls (already available as a transitive dependency via FastMCP)
