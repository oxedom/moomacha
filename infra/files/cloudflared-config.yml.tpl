tunnel: __TUNNEL_ID__
credentials-file: /etc/cloudflared/__TUNNEL_ID__.json

ingress:
  # Public exposure limited to the Zulip outgoing-webhook path; the management API
  # (/agents) and /healthz are blocked at Cloudflare's edge and never reach the app.
  - hostname: __INGRESS_HOSTNAME__
    path: ^/zulip/incoming/?$
    service: http://localhost:8000
  - service: http_status:404
