from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("'\"")
    return values


def main() -> None:
    values = _env_values()
    code = (ROOT / "scripts" / "live_dm_playwright.js").read_text()
    code = code.replace("process.env.ZULIP_SITE", json.dumps(values["ZULIP_SITE"]))
    code = code.replace("process.env.ZULIP_BOT_EMAIL", json.dumps(values["ZULIP_BOT_EMAIL"]))
    # Keep secrets out of the playwright-cli argv. The browser session should
    # already be authenticated; if it is not, the JS fails before sending a DM.
    code = code.replace("process.env.ZULIP_MY_EMAIL", "undefined")
    code = code.replace("process.env.ZULIP_MY_PAS", "undefined")
    completed = subprocess.run(["playwright-cli", "run-code", code], cwd=ROOT, timeout=180)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
