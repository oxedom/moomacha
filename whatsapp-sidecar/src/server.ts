import http from 'node:http';
import type { MessageBus } from './bus.js';
import { isAuthorized, sseFrame } from './http-helpers.js';
import { logger } from './logger.js';
import type { ConnectionStatus } from './types.js';

export interface AccountView {
  id: string;
  label: string;
  status: ConnectionStatus;
}

export interface ServerDeps {
  bus: MessageBus;
  token: string;
  /** Current account list + status, evaluated per request. */
  accounts: () => AccountView[];
  /** Heartbeat interval for SSE streams, ms. 0 disables. */
  heartbeatMs?: number;
}

/**
 * Loopback HTTP API over the message bus. Routes:
 *   GET /health                     — liveness, unauthenticated
 *   GET /accounts                   — account ids + connection status
 *   GET /recent?account=&limit=     — buffered backlog as JSON
 *   GET /stream?account=&last=N     — live SSE feed (optionally replaying N)
 * Every route except /health requires `Authorization: Bearer <token>`.
 */
export function createServer(deps: ServerDeps): http.Server {
  const { bus, token } = deps;
  const heartbeatMs = deps.heartbeatMs ?? 25000;

  return http.createServer((req, res) => {
    const url = new URL(req.url || '/', 'http://localhost');
    const route = url.pathname;

    if (req.method !== 'GET') {
      return json(res, 405, { error: 'method not allowed' });
    }

    if (route === '/health') {
      return json(res, 200, { ok: true });
    }

    if (!isAuthorized(req.headers.authorization, token)) {
      return json(res, 401, { error: 'unauthorized' });
    }

    if (route === '/accounts') {
      return json(res, 200, { accounts: deps.accounts() });
    }

    if (route === '/recent') {
      const account = url.searchParams.get('account') || undefined;
      const limitRaw = url.searchParams.get('limit');
      const limit = limitRaw !== null ? Number(limitRaw) : undefined;
      return json(res, 200, { messages: bus.recent(limit, account) });
    }

    if (route === '/stream') {
      return handleStream(req, res, bus, url, heartbeatMs);
    }

    return json(res, 404, { error: 'not found' });
  });
}

function handleStream(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  bus: MessageBus,
  url: URL,
  heartbeatMs: number,
): void {
  const account = url.searchParams.get('account') || undefined;
  const lastRaw = url.searchParams.get('last');
  const last = lastRaw !== null ? Number(lastRaw) : 0;

  res.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  });
  res.write(sseFrame(null, ': connected'));

  // Replay backlog first, so a reconnecting subscriber can catch up.
  if (last > 0) {
    for (const msg of bus.recent(last, account)) {
      res.write(sseFrame('message', msg));
    }
  }

  const unsub = bus.subscribe((msg) => {
    if (account && msg.account_id !== account) return;
    res.write(sseFrame('message', msg));
  });

  const hb =
    heartbeatMs > 0
      ? setInterval(() => res.write(sseFrame(null, ': hb')), heartbeatMs)
      : undefined;

  const close = () => {
    unsub();
    if (hb) clearInterval(hb);
  };
  req.on('close', close);
  req.on('error', close);
}

function json(res: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

/** Start listening and log the bound address. */
export function startServer(
  server: http.Server,
  host: string,
  port: number,
): Promise<void> {
  return new Promise((resolve) => {
    server.listen(port, host, () => {
      logger.info({ host, port }, `sidecar HTTP listening on ${host}:${port}`);
      resolve();
    });
  });
}
