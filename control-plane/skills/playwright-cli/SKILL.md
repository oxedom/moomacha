---
name: playwright-cli
description: Use when the agent needs to automate a browser — navigate websites, take screenshots, fill forms, or extract page content.
---

# Browser Automation with local_browser_* tools

Use these tools to control a headed Playwright browser session. The session is automatically scoped to the current agent — no session ID needed.

## Quick Start

```
local_browser_open (url: "https://example.com")
local_browser_snapshot
local_browser_click (ref: "e3")
local_browser_fill (ref: "e5", text: "hello@example.com")
local_browser_screenshot
local_browser_close
```

## Available Tools

### Navigation

| Tool | Purpose |
|---|---|
| `local_browser_open` | Open a new browser session, optionally navigating to a URL |
| `local_browser_goto` | Navigate the current session to a URL |
| `local_browser_close` | Close the current browser page |

### Interaction

| Tool | Purpose |
|---|---|
| `local_browser_snapshot` | Capture the accessibility tree and element refs for the current page |
| `local_browser_click` | Click an element by ref (from snapshot) |
| `local_browser_fill` | Fill an editable element by ref; optionally press Enter after |
| `local_browser_type` | Type text into the currently focused element |
| `local_browser_press` | Press a keyboard key (e.g. Enter, Escape, ArrowDown) |

### Inspection

| Tool | Purpose |
|---|---|
| `local_browser_screenshot` | Take a screenshot of the page or an element |
| `local_browser_eval` | Evaluate JavaScript in the page, optionally against an element ref |

## Workflow Pattern

1. `local_browser_open` with a URL
2. `local_browser_snapshot` to see element refs
3. Interact with refs (`local_browser_click`, `local_browser_fill`, etc.)
4. `local_browser_snapshot` again to verify state
5. `local_browser_close` when done

## Session Scoping

The browser session is automatically derived from the agent ID — you do not need to pass a session identifier. Each agent gets its own isolated browser.

## Not Available

The following playwright-cli features have no `local_browser_*` counterpart and cannot be used:

- Tab management (tab-new, tab-list, tab-close, tab-select)
- Network route mocking (route, unroute)
- Tracing (tracing-start, tracing-stop)
- Video recording (video-start, video-stop)
- Storage state management (state-save, state-load, cookie-*, localstorage-*)
