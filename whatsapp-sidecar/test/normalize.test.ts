import { describe, it, expect } from 'vitest';
import { normalizeMessage } from '../src/normalize.js';

// Minimal stand-ins for Baileys' proto.IWebMessageInfo. We only build the
// fields normalizeMessage reads, matching the shapes seen on messages.upsert.
function dmText(overrides: Record<string, unknown> = {}) {
  return {
    key: { remoteJid: '14155550000@s.whatsapp.net', id: 'ABC123', fromMe: false },
    pushName: 'Alice',
    messageTimestamp: 1700000000,
    message: { conversation: 'hello there' },
    ...overrides,
  };
}

describe('normalizeMessage', () => {
  it('normalizes a plain DM text message', () => {
    const out = normalizeMessage('acct1', dmText());
    expect(out).toEqual({
      account_id: 'acct1',
      id: 'ABC123',
      chat_jid: '14155550000@s.whatsapp.net',
      is_group: false,
      sender: '14155550000@s.whatsapp.net',
      sender_name: 'Alice',
      text: 'hello there',
      type: 'text',
      timestamp: '2023-11-14T22:13:20.000Z',
      from_me: false,
    });
  });

  it('marks group messages and uses the participant as sender', () => {
    const out = normalizeMessage('acct1', {
      key: {
        remoteJid: '123456789@g.us',
        participant: '14155550000@s.whatsapp.net',
        id: 'G1',
        fromMe: false,
      },
      pushName: 'Bob',
      messageTimestamp: 1700000000,
      message: { extendedTextMessage: { text: 'group hi' } },
    });
    expect(out?.is_group).toBe(true);
    expect(out?.chat_jid).toBe('123456789@g.us');
    expect(out?.sender).toBe('14155550000@s.whatsapp.net');
    expect(out?.text).toBe('group hi');
    expect(out?.type).toBe('text');
  });

  it('extracts image caption and tags type=image', () => {
    const out = normalizeMessage('acct1', {
      key: { remoteJid: '14155550000@s.whatsapp.net', id: 'IMG', fromMe: false },
      pushName: 'Alice',
      messageTimestamp: 1700000000,
      message: { imageMessage: { caption: 'look at this' } },
    });
    expect(out?.text).toBe('look at this');
    expect(out?.type).toBe('image');
  });

  it('tags an audio message type=audio even with empty text', () => {
    const out = normalizeMessage('acct1', {
      key: { remoteJid: '14155550000@s.whatsapp.net', id: 'AUD', fromMe: false },
      pushName: 'Alice',
      messageTimestamp: 1700000000,
      message: { audioMessage: { ptt: true } },
    });
    expect(out?.type).toBe('audio');
    expect(out?.text).toBe('');
  });

  it('falls back to the sender number when pushName is absent', () => {
    const out = normalizeMessage('acct1', dmText({ pushName: undefined }));
    expect(out?.sender_name).toBe('14155550000');
  });

  it('honors fromMe', () => {
    const out = normalizeMessage(
      'acct1',
      dmText({ key: { remoteJid: '14155550000@s.whatsapp.net', id: 'X', fromMe: true } }),
    );
    expect(out?.from_me).toBe(true);
  });

  it('returns null for status broadcasts', () => {
    expect(
      normalizeMessage('acct1', dmText({ key: { remoteJid: 'status@broadcast', id: 'S' } })),
    ).toBeNull();
  });

  it('returns null when there is no remoteJid', () => {
    expect(normalizeMessage('acct1', dmText({ key: { id: 'S' } }))).toBeNull();
  });

  it('returns null for a message envelope with no message payload (receipt/protocol)', () => {
    expect(normalizeMessage('acct1', dmText({ message: null }))).toBeNull();
  });

  it('returns null for a text-less protocol message (e.g. reaction/key)', () => {
    const out = normalizeMessage('acct1', {
      key: { remoteJid: '14155550000@s.whatsapp.net', id: 'P', fromMe: false },
      messageTimestamp: 1700000000,
      message: { senderKeyDistributionMessage: {} },
    });
    expect(out).toBeNull();
  });
});
