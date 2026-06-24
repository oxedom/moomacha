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


def _redact(text: str, values: dict[str, str]) -> str:
    for key in ("ZULIP_MY_EMAIL", "ZULIP_MY_PAS"):
        value = values.get(key)
        if value:
            text = text.replace(value, "<redacted>")
    return text


def main() -> None:
    values = _env_values()
    code = f"""
async page => {{
  await page.goto({json.dumps(values["ZULIP_SITE"].rstrip("/") + "/login/")}, {{ waitUntil: "domcontentloaded" }});
  await page.locator("input[name=email], input[type=email]").first().fill({json.dumps(values["ZULIP_MY_EMAIL"])});
  await page.locator("input[name=password], input[type=password]").first().fill({json.dumps(values["ZULIP_MY_PAS"])});
  await page.locator("button[type=submit], input[type=submit]").first().click();
  await page.waitForFunction(() => !location.pathname.includes("/login"), null, {{ timeout: 30000 }});
  return await page.evaluate(() => ({{ href: location.href, title: document.title }}));
}}
""".strip()
    completed = subprocess.run(
        ["playwright-cli", "run-code", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    output = _redact(completed.stdout + completed.stderr, values)
    if output:
        print(output, end="")
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
