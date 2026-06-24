import { MessageBus } from './bus.js';
import { loadConfig } from './config.js';
import { AccountConnection } from './connection.js';
import { createServer, startServer, type AccountView } from './server.js';
import { logger } from './logger.js';

/**
 * Entry point: load accounts, open one read-only WhatsApp connection each,
 * fan their messages into a shared bus, and serve the bus over loopback HTTP.
 */
async function main(): Promise<void> {
  const config = loadConfig();

  if (!config.token) {
    logger.error(
      'WHATSAPP_SIDECAR_TOKEN is required (the bearer for the HTTP API). Refusing to start.',
    );
    process.exit(1);
  }
  if (config.accounts.length === 0) {
    logger.warn(
      'No accounts configured (accounts.json empty/missing). Serving HTTP with zero connections.',
    );
  }

  const bus = new MessageBus(config.bufferSize);

  const connections = config.accounts.map(
    (acct) => new AccountConnection(acct, config.storeDir, bus),
  );

  const accountsView = (): AccountView[] =>
    connections.map((c) => ({ id: c.id, label: c.label, status: c.getStatus() }));

  const server = createServer({ bus, token: config.token, accounts: accountsView });
  await startServer(server, config.host, config.port);

  // Start connections concurrently; one failing to pair must not block the rest.
  await Promise.allSettled(
    connections.map((c) =>
      c.start().catch((err) => {
        logger.error({ account: c.id, err }, `failed to start account ${c.id}`);
      }),
    ),
  );

  const shutdown = async (sig: string) => {
    logger.info({ sig }, 'shutting down');
    await Promise.allSettled(connections.map((c) => c.stop()));
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 2000).unref();
  };
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('SIGINT', () => void shutdown('SIGINT'));
}

main().catch((err) => {
  logger.error({ err }, 'fatal');
  process.exit(1);
});
