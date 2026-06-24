import { timingSafeEqual } from 'node:crypto';

/**
 * Validate an `Authorization: Bearer <token>` header against the configured
 * token. Closed by default: an empty configured token denies everything, so a
 * misconfigured sidecar never silently runs unauthenticated.
 */
export function isAuthorized(
  header: string | undefined,
  token: string,
): boolean {
  if (!token) return false;
  if (!header || !header.startsWith('Bearer ')) return false;
  const presented = header.slice('Bearer '.length);
  const a = Buffer.from(presented);
  const b = Buffer.from(token);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

/**
 * Build a Server-Sent-Events frame. With an `event` name, `data` is JSON-
 * encoded; with `event === null`, `data` is emitted verbatim (used for `:`
 * heartbeat comment lines).
 */
export function sseFrame(event: string | null, data: unknown): string {
  if (event === null) {
    return `${data}\n\n`;
  }
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}
