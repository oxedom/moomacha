resource "hcloud_ssh_key" "acp" {
  name       = "agent-control-pane"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "hcloud_firewall" "acp" {
  name = "agent-control-pane-fw"

  # Tailscale direct connections (UDP 41641). Falls back to DERP relay if blocked.
  rule {
    direction  = "in"
    protocol   = "udp"
    port       = "41641"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # ICMP for reachability/debugging.
  rule {
    direction  = "in"
    protocol   = "icmp"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # Optional Tailscale-independent SSH backdoor, only if admin_ip_cidr is set.
  dynamic "rule" {
    for_each = var.admin_ip_cidr == "" ? [] : [var.admin_ip_cidr]
    content {
      direction  = "in"
      protocol   = "tcp"
      port       = "22"
      source_ips = [rule.value]
    }
  }
  # NOTE: no public 80/443/8000 — the webhook arrives via cloudflared's outbound
  # tunnel; admin/SSH arrive via Tailscale.
}

resource "hcloud_server" "acp" {
  name         = "agent-control-pane"
  server_type  = var.server_type
  image        = "ubuntu-24.04"
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.acp.id]
  firewall_ids = [hcloud_firewall.acp.id]

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    acp_user           = var.acp_user
    tailscale_auth_key = var.tailscale_auth_key
    tailscale_hostname = var.tailscale_hostname
    tailscale_tags     = var.tailscale_tags
  })

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  labels = {
    project = "agent-control-pane"
  }
}
