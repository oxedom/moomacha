import pino from 'pino';

/** Sidecar's own logger. LOG_LEVEL controls verbosity (default: info). */
export const logger = pino({
  level: process.env.LOG_LEVEL || 'info',
});

/** Baileys is noisy and demands its own pino instance; keep it silent. */
export const baileysLogger = pino({ level: 'silent' });
