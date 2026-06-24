import fs from 'node:fs';
import path from 'node:path';

import {
  makeWASocket,
  Browsers,
  DisconnectReason,
  fetchLatestWaWebVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
// @ts-expect-error no type declarations
import qrcode from 'qrcode-terminal';

import type { MessageBus } from './bus.js';
import { normalizeMessage } from './normalize.js';
import { baileysLogger, logger } from './logger.js';
import type { AccountConfig, ConnectionStatus } from './types.js';

/**
 * One read-only WhatsApp connection for a single account.
 *
 * Wires exactly three events: `connection.update` (lifecycle + QR pairing),
 * `messages.upsert` (the inbound feed → bus), and `creds.update` (persist
 * session). There is deliberately **no send path** — this is a monitor.
 * Auto-reconnects on every disconnect except `loggedOut` (needs re-pair).
 */
export class AccountConnection {
  readonly id: string;
  readonly label: string;
  private status: ConnectionStatus = 'connecting';
  private readonly authDir: string;
  private sock: ReturnType<typeof makeWASocket> | undefined;
  private stopped = false;

  constructor(
    account: AccountConfig,
    storeDir: string,
    private readonly bus: MessageBus,
  ) {
    this.id = account.id;
    this.label = account.label ?? account.id;
    this.authDir = account.authDir
      ? path.resolve(account.authDir)
      : path.join(storeDir, account.id);
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  async start(): Promise<void> {
    fs.mkdirSync(this.authDir, { recursive: true });
    await this.connect();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    try {
      this.sock?.end(undefined);
    } catch {
      // best-effort teardown
    }
  }

  private async connect(): Promise<void> {
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);

    const { version } = await fetchLatestWaWebVersion({}).catch(() => ({
      version: undefined,
    }));

    const sock = makeWASocket({
      version,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, baileysLogger),
      },
      printQRInTerminal: false,
      logger: baileysLogger,
      browser: Browsers.macOS('Chrome'),
      // Receive-only: never mark chats read, never send presence.
      markOnlineOnConnect: false,
    });
    this.sock = sock;

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        this.status = 'pairing';
        logger.warn(
          { account: this.id },
          `[${this.id}] Pairing required — scan this QR with WhatsApp ` +
            `(Settings → Linked Devices → Link a Device):`,
        );
        qrcode.generate(qr, { small: true });
      }

      if (connection === 'open') {
        this.status = 'open';
        logger.info({ account: this.id }, `[${this.id}] connected`);
      } else if (connection === 'close') {
        this.status = 'close';
        const reason = (
          lastDisconnect?.error as { output?: { statusCode?: number } }
        )?.output?.statusCode;
        const loggedOut = reason === DisconnectReason.loggedOut;
        logger.warn(
          { account: this.id, reason, loggedOut },
          `[${this.id}] connection closed`,
        );
        if (this.stopped) return;
        if (loggedOut) {
          logger.error(
            { account: this.id },
            `[${this.id}] logged out — delete ${this.authDir} and re-pair`,
          );
          return;
        }
        // Reconnect; one delayed retry if the immediate attempt throws.
        this.connect().catch(() => {
          setTimeout(() => {
            this.connect().catch((err) =>
              logger.error(
                { account: this.id, err },
                `[${this.id}] reconnect retry failed`,
              ),
            );
          }, 5000);
        });
      }
    });

    sock.ev.on('messages.upsert', ({ messages }) => {
      for (const raw of messages) {
        const msg = normalizeMessage(this.id, raw);
        if (msg) this.bus.publish(msg);
      }
    });
  }
}
