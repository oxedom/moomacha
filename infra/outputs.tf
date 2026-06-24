output "server_ipv4" {
  value = hcloud_server.acp.ipv4_address
}

output "server_ipv6" {
  value = hcloud_server.acp.ipv6_address
}

output "server_id" {
  value = hcloud_server.acp.id
}

output "tailscale_hostname" {
  description = "MagicDNS name to SSH to (once the box has joined the tailnet)."
  value       = var.tailscale_hostname
}
