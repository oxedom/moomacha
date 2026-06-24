import fs from 'node:fs';
import path from 'node:path';
import type { AccountConfig } from './types.js';

export interface SidecarConfig {
  /** Loopback bind host. Never expose publicly — read endpoints are unguarded beyond the bearer. */
  host: string;
  port: number;
  /** Bearer token required on every HTTP request. */
  token: string;
  /** Root dir holding per-account Baileys auth state (`<storeDir>/<account_id>`). */
  storeDir: string;
  /** Ring-buffer size for stream backlog. */
  bufferSize: number;
  accounts: AccountConfig[];
}

/**
 * Resolve config from env + an accounts JSON file.
 *
 * - `WHATSAPP_SIDECAR_TOKEN` (required) — bearer for the HTTP API.
 * - `WHATSAPP_SIDECAR_HOST` / `_PORT` — bind address (default 127.0.0.1:8765).
 * - `WHATSAPP_SIDECAR_STORE` — auth-state root (default `<cwd>/store`).
 * - `WHATSAPP_SIDECAR_ACCOUNTS` — path to accounts JSON (default `<cwd>/accounts.json`).
 * - `WHATSAPP_SIDECAR_BUFFER` — ring-buffer size (default 1000).
 */
export function loadConfig(env = process.env): SidecarConfig {
  const storeDir = path.resolve(
    env.WHATSAPP_SIDECAR_STORE || path.join(process.cwd(), 'store'),
  );
  const accountsPath = path.resolve(
    env.WHATSAPP_SIDECAR_ACCOUNTS || path.join(process.cwd(), 'accounts.json'),
  );

  let accounts: AccountConfig[] = [];
  if (fs.existsSync(accountsPath)) {
    const parsed = JSON.parse(fs.readFileSync(accountsPath, 'utf8'));
    accounts = Array.isArray(parsed) ? parsed : parsed.accounts ?? [];
  }

  return {
    host: env.WHATSAPP_SIDECAR_HOST || '127.0.0.1',
    port: Number(env.WHATSAPP_SIDECAR_PORT || 8765),
    token: env.WHATSAPP_SIDECAR_TOKEN || '',
    storeDir,
    bufferSize: Number(env.WHATSAPP_SIDECAR_BUFFER || 1000),
    accounts,
  };
}
