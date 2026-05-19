# Full-Width Chat Layout

## Summary

Remove the fixed `max-width: 800px` from `#app` so the chat fills the
entire viewport width. The user controls zoom/scale at the browser level.

## Change

**File:** `joinora/frontend/style.css`

**`#app` rule — remove `max-width: 800px;`**

The remaining properties (`display: flex`, `flex-direction: column`,
`height: 100vh`, `margin: 0 auto`) stay unchanged.

## What stays the same

- `.message` keeps `max-width: 85%` — bubbles don't stretch edge-to-edge.
- `.join-card` keeps `max-width: 400px; width: 90%` — stays centered and compact.
- No responsive breakpoints — browser-level scaling handles this.
- All other layout (flexbox column, message alignment, input area, header) unchanged.

## Scope

One CSS property deletion. No HTML or JS changes.
