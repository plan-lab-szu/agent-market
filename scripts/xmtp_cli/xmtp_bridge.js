const fs = require("fs");
const path = require("path");
const { createHash } = require("crypto");
const readline = require("readline");
const { Client, IdentifierKind, ConsentState } = require("@xmtp/node-sdk");
const { Wallet } = require("ethers");

const PRIVATE_KEY =
  process.env.XMTP_PRIVATE_KEY ||
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
const DB_PATH = process.env.XMTP_DB_PATH;
const DB_KEY_HEX = process.env.XMTP_DB_KEY;
const API_URL = process.env.XMTP_API_URL;
const HISTORY_SYNC_URL = process.env.XMTP_HISTORY_SYNC_URL;
const TIMEOUT_MS = Number(process.env.XMTP_RECV_TIMEOUT_MS || 30000);

function loadInfraEnv() {
  const envPath =
    process.env.XMTP_INFRA_ENV ||
    path.resolve(__dirname, "..", "infrastructure", "env.json");
  try {
    return JSON.parse(fs.readFileSync(envPath, "utf8"));
  } catch (err) {
    return null;
  }
}

function resolveClientEnv() {
  let env = process.env.XMTP_ENV || "";
  let apiUrl = API_URL;
  if (!env && !apiUrl) {
    const infraEnv = loadInfraEnv();
    if (infraEnv?.xmtp_host && infraEnv?.xmtp_port) {
      env = "local";
      apiUrl = `http://${infraEnv.xmtp_host}:${infraEnv.xmtp_port}`;
    }
  }
  if (!env) {
    env = "dev";
  }
  return { env, apiUrl };
}

async function createClient() {
  const wallet = new Wallet(PRIVATE_KEY);
  const { env, apiUrl } = resolveClientEnv();
  const useDb = DB_PATH !== "null";
  const dbKey = useDb
    ? DB_KEY_HEX
      ? Uint8Array.from(Buffer.from(DB_KEY_HEX.replace(/^0x/, ""), "hex"))
      : Uint8Array.from(createHash("sha256").update(PRIVATE_KEY).digest())
    : undefined;
  const baseOptions = {
    env,
    dbEncryptionKey: dbKey,
    dbPath: useDb ? DB_PATH : null,
  };
  if (apiUrl) {
    baseOptions.apiUrl = apiUrl;
  }
  if (HISTORY_SYNC_URL) {
    baseOptions.historySyncUrl = HISTORY_SYNC_URL;
  }
  const signer = {
    type: "EOA",
    getIdentifier: () => ({
      identifier: wallet.address,
      identifierKind: IdentifierKind.Ethereum,
    }),
    signMessage: async (message) => {
      const signature = await wallet.signMessage(message);
      return Uint8Array.from(Buffer.from(signature.replace(/^0x/, ""), "hex"));
    },
  };
  return Client.create(signer, baseOptions);
}

async function waitForMessage(client, targetId) {
  const existing = client.conversations.getMessageById(targetId);
  if (existing) {
    return existing.id;
  }
  await client.conversations.syncAll([ConsentState.Allowed, ConsentState.Unknown]);
  const existingAfter = client.conversations.getMessageById(targetId);
  if (existingAfter) {
    return existingAfter.id;
  }

  const deadline = Date.now() + TIMEOUT_MS;
  const stream = await client.conversations.streamAllMessages({
    consentStates: [ConsentState.Allowed, ConsentState.Unknown],
  });
  for await (const message of stream) {
    if (message.id === targetId) {
      return message.id;
    }
    if (Date.now() > deadline) {
      throw new Error("XMTP recv timeout");
    }
  }
  throw new Error("XMTP recv stream ended");
}

async function main() {
  const peer = process.env.XMTP_PEER;
  if (!peer) {
    throw new Error("XMTP_PEER is required");
  }
  const client = await createClient();
  const dm = await client.conversations.createDmWithIdentifier({
    identifier: peer,
    identifierKind: IdentifierKind.Ethereum,
  });

  process.stdout.write("READY\n");

  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    const payload = line || "";
    try {
      const messageId = await dm.sendText(payload);
      const receivedId = await waitForMessage(client, messageId);
      process.stdout.write(`${receivedId}\n`);
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      process.stdout.write(`ERROR:${msg}\n`);
    }
  }
}

main().catch((err) => {
  const msg = err && err.message ? err.message : String(err);
  process.stdout.write(`ERROR:${msg}\n`);
  process.exit(1);
});
