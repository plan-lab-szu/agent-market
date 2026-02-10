const fs = require("fs");
const path = require("path");
const { createHash } = require("crypto");
const { Client, IdentifierKind } = require("@xmtp/node-sdk");
const { Wallet } = require("ethers");

const LAST_MSG_PATH = process.env.XMTP_LAST_MSG_PATH || "/tmp/xmtp_last_msg_id";
const PRIVATE_KEY =
  process.env.XMTP_PRIVATE_KEY ||
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
const DB_PATH = process.env.XMTP_DB_PATH;
const DB_KEY_HEX = process.env.XMTP_DB_KEY;
const PEER_PRIVATE_KEY = process.env.XMTP_PEER_PRIVATE_KEY;
const PEER_DB_PATH = process.env.XMTP_PEER_DB_PATH;
const PEER_DB_KEY_HEX = process.env.XMTP_PEER_DB_KEY;
const API_URL = process.env.XMTP_API_URL;
const HISTORY_SYNC_URL = process.env.XMTP_HISTORY_SYNC_URL;

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

async function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

async function main() {
  const payload = await readStdin();
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
  if (PEER_PRIVATE_KEY) {
    const peerWallet = new Wallet(PEER_PRIVATE_KEY);
    const peerUseDb = PEER_DB_PATH !== "null";
    const peerDbKey = peerUseDb
      ? PEER_DB_KEY_HEX
        ? Uint8Array.from(Buffer.from(PEER_DB_KEY_HEX.replace(/^0x/, ""), "hex"))
        : Uint8Array.from(
            createHash("sha256").update(PEER_PRIVATE_KEY).digest()
          )
      : undefined;
    const peerOptions = {
      env,
      dbEncryptionKey: peerDbKey,
      dbPath: peerUseDb ? PEER_DB_PATH : null,
    };
    if (apiUrl) {
      peerOptions.apiUrl = apiUrl;
    }
    if (HISTORY_SYNC_URL) {
      peerOptions.historySyncUrl = HISTORY_SYNC_URL;
    }
    const peerSigner = {
      type: "EOA",
      getIdentifier: () => ({
        identifier: peerWallet.address,
        identifierKind: IdentifierKind.Ethereum,
      }),
      signMessage: async (message) => {
        const signature = await peerWallet.signMessage(message);
        return Uint8Array.from(
          Buffer.from(signature.replace(/^0x/, ""), "hex")
        );
      },
    };
    await Client.create(peerSigner, peerOptions);
  }
  const client = await Client.create(signer, baseOptions);
  const peer = process.env.XMTP_PEER;
  if (!peer) {
    throw new Error("XMTP_PEER is required for send");
  }
  const dm = await client.conversations.createDmWithIdentifier({
    identifier: peer,
    identifierKind: IdentifierKind.Ethereum,
  });
  const messageId = await dm.sendText(payload || "");
  fs.writeFileSync(LAST_MSG_PATH, messageId, "utf8");
  process.stdout.write(messageId);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
