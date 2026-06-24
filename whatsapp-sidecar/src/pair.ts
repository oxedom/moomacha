/**
 * One-shot pairing for an account, using a **pairing code** (more reliable than
 * scanning a rotating terminal QR). Writes the Baileys session into the same
 * store the sidecar reads (`<store>/<account_id>`), then exits. The running
 * sidecar picks up the saved creds on its next (re)connect.
 *
 * Usage:
 *   tsx src/pair.ts <account_id> <phone_e164_digits>
 *   # e.g. tsx src/pair.ts test 9725551234   (country code + number, no + or spaces)
 *
 * On WhatsApp: Settings → Linked Devices → Link a Device →
 * "Link with phone number instead" → enter the printed code.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

import {
  makeWASocket,
  Browsers,
  DisconnectReason,
  fetchLatestWaWebVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import pino from 'pino';

// Baileys 6.x bug: getPlatformId sends a charCode instead of the enum value,
// which makes pairing-code linking fail ("couldn't link device"). Fixed in 7.x.
// ESM `import *` namespaces are read-only, so patch via createRequire.
const _require = createRequire(import.meta.url);
const _generics = _require('@whiskeysockets/baileys/lib/Utils/generics') as Record<
  string,
  unknown
>;
const { proto } = _require('@whiskeysockets/baileys') as { proto: any };
_generics.getPlatformId = (browser: string): string => {
  const t =
    proto.DeviceProps.PlatformType[
      browser.toUpperCase() as keyof typeof proto.DeviceProps.PlatformType
    ];
  return t ? t.toString() : '1';
};

const logger = pino({ level: 'silent' });

async function main() {
  const accountId = process.argv[2];
  const phone = process.argv[3]?.replace(/[^0-9]/g, '');
  if (!accountId || !phone) {
    console.error('usage: tsx src/pair.ts <account_id> <phone_digits_with_country_code>');
    process.exit(2);
  }

  const storeDir = path.resolve(
    process.env.WHATSAPP_SIDECAR_STORE || path.join(process.cwd(), 'store'),
  );
  const authDir = path.join(storeDir, accountId);
  fs.mkdirSync(authDir, { recursive: true });

  await connect(accountId, phone, authDir);
}

async function connect(
  accountId: string,
  phone: string,
  authDir: string,
  isReconnect = false,
): Promise<void> {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);

  if (state.creds.registered && !isReconnect) {
    console.log(`✓ account "${accountId}" already paired (${authDir}). Nothing to do.`);
    process.exit(0);
  }

  const { version } = await fetchLatestWaWebVersion({}).catch(() => ({
    version: undefined,
  }));

  const sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    printQRInTerminal: false,
    logger,
    browser: Browsers.macOS('Chrome'),
    markOnlineOnConnect: false,
  });

  sock.ev.on('creds.update', saveCreds);

  // Request the pairing code once, shortly after the socket initializes.
  if (!state.creds.registered && !isReconnect) {
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(phone);
        const pretty = code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
        console.log('\n==================================================');
        console.log(`  WhatsApp pairing code for "${accountId}":  ${pretty}`);
        console.log('==================================================');
        console.log('  On the phone whose number you entered:');
        console.log('  1. WhatsApp → Settings → Linked Devices → Link a Device');
        console.log('  2. Tap "Link with phone number instead"');
        console.log(`  3. Enter: ${pretty}\n`);
      } catch (err) {
        console.error('Failed to request pairing code:', (err as Error).message);
        process.exit(1);
      }
    }, 3000);
  }

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect } = update;

    if (connection === 'open') {
      console.log(`\n✓ Paired! Session saved to ${authDir}`);
      console.log('  Start the sidecar (npm run serve) and it will connect with these creds.');
      setTimeout(() => process.exit(0), 1500);
    } else if (connection === 'close') {
      const reason = (lastDisconnect?.error as { output?: { statusCode?: number } })
        ?.output?.statusCode;
      if (reason === DisconnectReason.loggedOut) {
        console.error('\n✗ Logged out during pairing. Delete the store dir and retry.');
        process.exit(1);
      } else if (reason === 515) {
        // Stream error right after pairing — reconnect to finish registration.
        console.log('⟳ Finishing handshake (515)…');
        connect(accountId, phone, authDir, true);
      } else {
        // Transient close during the handshake — retry the handshake.
        console.log(`⟳ Reconnecting (reason ${reason ?? 'unknown'})…`);
        connect(accountId, phone, authDir, true);
      }
    }
  });
}

main().catch((err) => {
  console.error('pairing failed:', err);
  process.exit(1);
});
