import { describe, it, expect } from 'vitest';
import { isAuthorized, sseFrame } from '../src/http-helpers.js';

describe('isAuthorized', () => {
  it('accepts a matching Bearer token', () => {
    expect(isAuthorized('Bearer s3cret', 's3cret')).toBe(true);
  });

  it('rejects a wrong token', () => {
    expect(isAuthorized('Bearer nope', 's3cret')).toBe(false);
  });

  it('rejects a missing header', () => {
    expect(isAuthorized(undefined, 's3cret')).toBe(false);
  });

  it('rejects a non-Bearer scheme', () => {
    expect(isAuthorized('Basic s3cret', 's3cret')).toBe(false);
  });

  it('rejects when token differs only in length (no prefix match)', () => {
    expect(isAuthorized('Bearer s3cre', 's3cret')).toBe(false);
    expect(isAuthorized('Bearer s3cretx', 's3cret')).toBe(false);
  });

  it('when no token is configured, denies all (closed by default)', () => {
    expect(isAuthorized('Bearer anything', '')).toBe(false);
    expect(isAuthorized(undefined, '')).toBe(false);
  });
});

describe('sseFrame', () => {
  it('formats a named JSON event with a trailing blank line', () => {
    expect(sseFrame('message', { id: 'A' })).toBe(
      'event: message\ndata: {"id":"A"}\n\n',
    );
  });

  it('emits multi-line data on the same data line as compact JSON', () => {
    // JSON.stringify produces single-line output, so no embedded newlines.
    const frame = sseFrame('message', { text: 'a\nb' });
    expect(frame).toBe('event: message\ndata: {"text":"a\\nb"}\n\n');
  });

  it('supports a comment/heartbeat frame when event is null', () => {
    expect(sseFrame(null, ':hb')).toBe(':hb\n\n');
  });
});
