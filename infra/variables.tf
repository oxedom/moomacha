variable "hcloud_token" {
  description = "Hetzner Cloud API token (Read & Write). Supply via TF_VAR_hcloud_token from 1Password."
  type        = string
  sensitive   = true
}

variable "tailscale_auth_key" {
  description = "Ephemeral, single-use, pre-approved Tailscale auth key tagged tag:agent-server."
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Hetzner location."
  type        = string
  default     = "nbg1"
}

variable "server_type" {
  description = "Hetzner server type. cx23 = x86/2vCPU/4GB/40GB, cheapest EU at ~EUR4.99/mo."
  type        = string
  default     = "cx23"
}

variable "ssh_public_key_path" {
  description = "Public key injected for root recovery (reached over Tailscale, not a public port)."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "tailscale_hostname" {
  description = "Tailscale machine name (also the MagicDNS name used for SSH)."
  type        = string
  default     = "agent-control-pane"
}

variable "tailscale_tags" {
  description = "Comma-separated Tailscale ACL tags to advertise."
  type        = string
  default     = "tag:agent-server"
}

variable "acp_user" {
  description = "Non-root box user that owns the app checkout and runs the services."
  type        = string
  default     = "acp"
}

variable "admin_ip_cidr" {
  description = "Optional: open public TCP 22 to this CIDR as a Tailscale-independent SSH backdoor. Empty = no public SSH."
  type        = string
  default     = ""
}
