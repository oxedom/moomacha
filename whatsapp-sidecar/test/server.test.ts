import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import type { AddressInfo } from 'node:net';
import { MessageBus } from '../src/bus.js';
import { createServer } from '../src/server.js';
import type { NormalizedMessage } from '../src/types.js';

const TOKEN = 'test-token';

function mk(over: Partial<NormalizedMessage> = {}): NormalizedMessage {
  return {
    account_id: 'acct1',
    id: 'A',
    chat_jid: '1@s.whatsapp.net',
    is_group: false,
    sender: '1@s.whatsapp.net',
    sender_name: 'Alice',
    text: 'hi',
    type: 'text',
    timestamp: '2023-11-14T22:13:20.000Z',
    from_me: false,
    ...over,
  };
}

describe('http server', () => {
  let bus: MessageBus;
  let server: ReturnType<typeof createServer>;
  let base: string;

  beforeEach(async () => {
    bus = new MessageBus(10);
    server = createServer({
      bus,
      token: TOKEN,
      accounts: () => [{ id: 'acct1', label: 'Acct One', status: 'open' }],
    });
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
    const { port } = server.address() as AddressInfo;
    base = `http://127.0.0.1:${port}`;
  });

  afterEach(async () => {
    await new Promise<void>((r) => server.close(() => r()));
  });

  const auth = { headers: { authorization: `Bearer ${TOKEN}` } };

  it('GET /health returns ok without auth', async () => {
    const res = await fetch(`${base}/health`);
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true });
  });

  it('rejects unauthenticated requests to protected endpoints', async () => {
    expect((await fetch(`${base}/accounts`)).status).toBe(401);
    expect((await fetch(`${base}/recent`)).status).toBe(401);
    expect((await fetch(`${base}/stream`)).status).toBe(401);
  });

  it('GET /accounts lists accounts with status', async () => {
    const res = await fetch(`${base}/accounts`, auth);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      accounts: [{ id: 'acct1', label: 'Acct One', status: 'open' }],
    });
  });

  it('GET /recent returns buffered messages', async () => {
    bus.publish(mk({ id: 'A' }));
    bus.publish(mk({ id: 'B' }));
    const res = await fetch(`${base}/recent`, auth);
    const body = await res.json();
    expect(body.messages.map((m: NormalizedMessage) => m.id)).toEqual(['A', 'B']);
  });

  it('GET /recent?account= filters by account', async () => {
    bus.publish(mk({ id: 'A', account_id: 'acct1' }));
    bus.publish(mk({ id: 'B', account_id: 'other' }));
    const res = await fetch(`${base}/recent?account=acct1`, auth);
    const body = await res.json();
    expect(body.messages.map((m: NormalizedMessage) => m.id)).toEqual(['A']);
  });

  it('GET /recent?limit= caps the count', async () => {
    ['A', 'B', 'C'].forEach((id) => bus.publish(mk({ id })));
    const res = await fetch(`${base}/recent?limit=1`, auth);
    const body = await res.json();
    expect(body.messages.map((m: NormalizedMessage) => m.id)).toEqual(['C']);
  });

  it('404s unknown routes', async () => {
    expect((await fetch(`${base}/nope`, auth)).status).toBe(404);
  });

  it('GET /stream delivers live messages as SSE', async () => {
    const ac = new AbortController();
    const res = await fetch(`${base}/stream`, {
      headers: { authorization: `Bearer ${TOKEN}` },
      signal: ac.signal,
    });
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/event-stream');

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();

    // Publish after the stream is open so we exercise live delivery.
    setTimeout(() => bus.publish(mk({ id: 'LIVE' })), 20);

    let buf = '';
    // Read until we see the message frame.
    while (!buf.includes('"id":"LIVE"')) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
    }
    expect(buf).toContain('event: message');
    expect(buf).toContain('"id":"LIVE"');
    ac.abort();
  });

  it('GET /stream?last=N replays backlog before live', async () => {
    bus.publish(mk({ id: 'OLD' }));
    const ac = new AbortController();
    const res = await fetch(`${base}/stream?last=5`, {
      headers: { authorization: `Bearer ${TOKEN}` },
      signal: ac.signal,
    });
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (!buf.includes('"id":"OLD"')) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
    }
    expect(buf).toContain('"id":"OLD"');
    ac.abort();
  });
});
