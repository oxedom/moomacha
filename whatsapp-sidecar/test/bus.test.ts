import { describe, it, expect } from 'vitest';
import { MessageBus } from '../src/bus.js';
import type { NormalizedMessage } from '../src/types.js';

function mk(over: Partial<NormalizedMessage> = {}): NormalizedMessage {
  return {
    account_id: 'acct1',
    id: 'M' + Math.round(over.timestamp ? 0 : 1),
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

describe('MessageBus', () => {
  it('delivers published messages to subscribers', () => {
    const bus = new MessageBus();
    const got: NormalizedMessage[] = [];
    bus.subscribe((m) => got.push(m));

    const msg = mk({ id: 'A' });
    bus.publish(msg);

    expect(got).toEqual([msg]);
  });

  it('delivers to multiple subscribers', () => {
    const bus = new MessageBus();
    const a: NormalizedMessage[] = [];
    const b: NormalizedMessage[] = [];
    bus.subscribe((m) => a.push(m));
    bus.subscribe((m) => b.push(m));

    bus.publish(mk({ id: 'A' }));

    expect(a).toHaveLength(1);
    expect(b).toHaveLength(1);
  });

  it('stops delivering after unsubscribe', () => {
    const bus = new MessageBus();
    const got: NormalizedMessage[] = [];
    const unsub = bus.subscribe((m) => got.push(m));

    bus.publish(mk({ id: 'A' }));
    unsub();
    bus.publish(mk({ id: 'B' }));

    expect(got.map((m) => m.id)).toEqual(['A']);
  });

  it('retains recent messages in a ring buffer for late subscribers', () => {
    const bus = new MessageBus(3);
    bus.publish(mk({ id: 'A' }));
    bus.publish(mk({ id: 'B' }));

    expect(bus.recent().map((m) => m.id)).toEqual(['A', 'B']);
  });

  it('caps the ring buffer at its capacity, dropping oldest', () => {
    const bus = new MessageBus(2);
    bus.publish(mk({ id: 'A' }));
    bus.publish(mk({ id: 'B' }));
    bus.publish(mk({ id: 'C' }));

    expect(bus.recent().map((m) => m.id)).toEqual(['B', 'C']);
  });

  it('recent(n) returns only the last n messages', () => {
    const bus = new MessageBus(10);
    ['A', 'B', 'C', 'D'].forEach((id) => bus.publish(mk({ id })));

    expect(bus.recent(2).map((m) => m.id)).toEqual(['C', 'D']);
  });

  it('recent() can filter by account_id', () => {
    const bus = new MessageBus(10);
    bus.publish(mk({ id: 'A', account_id: 'acct1' }));
    bus.publish(mk({ id: 'B', account_id: 'other' }));
    bus.publish(mk({ id: 'C', account_id: 'acct1' }));

    expect(bus.recent(undefined, 'acct1').map((m) => m.id)).toEqual(['A', 'C']);
  });

  it('a throwing subscriber does not break delivery to others', () => {
    const bus = new MessageBus();
    const got: string[] = [];
    bus.subscribe(() => {
      throw new Error('boom');
    });
    bus.subscribe((m) => got.push(m.id));

    expect(() => bus.publish(mk({ id: 'A' }))).not.toThrow();
    expect(got).toEqual(['A']);
  });
});
