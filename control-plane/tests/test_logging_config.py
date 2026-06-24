import json
import logging

from control_plane.observability.logging_config import configure_logging


def test_configure_logging_json(capsys):
    configure_logging(json_logs=True)
    logging.getLogger("control_plane").info("hello", extra={"k": "v"})
    out = capsys.readouterr().out
    # last non-empty line is a JSON object with our event
    line = [ln for ln in out.splitlines() if ln.strip()][-1]
    parsed = json.loads(line)
    assert parsed["event"] == "hello"
