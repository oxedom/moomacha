from control_plane.services.artifact_html import CSP, inject_config


def test_inject_config_inserts_script_after_head():
    html = "<!doctype html><html><head><title>x</title></head><body>hi</body></html>"
    cfg = {"artifactId": "abc", "submitUrl": "/s", "statusUrl": "/st", "fullPayloadUrl": "/p"}
    out = inject_config(html, cfg)
    assert "window.__AGENT_UI__" in out
    assert '"artifactId":"abc"' in out or '"artifactId": "abc"' in out
    # config script must appear before the body so it runs before body scripts
    assert out.index("window.__AGENT_UI__") < out.index("<body>")
    # sugar helper present
    assert "window.AgentUI" in out


def test_inject_config_prepends_when_no_head():
    html = "<p>no head here</p>"
    out = inject_config(html, {"artifactId": "z"})
    assert out.startswith("<script>")
    assert "no head here" in out


def test_csp_is_restrictive_same_origin_connect():
    assert "connect-src 'self'" in CSP
    assert "cdn.tailwindcss.com" in CSP
    assert "unpkg.com" in CSP
