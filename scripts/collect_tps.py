#!/usr/bin/env python3
import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import threading

from eth_account import Account
from eth_account.messages import encode_defunct, encode_structured_data
from web3 import Web3

import fig3_config
import fig3_env
import x402
import x402_http


def parse_args():
    parser = argparse.ArgumentParser(description="采集 Fig.3(c) 吞吐量数据")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--concurrency", default="10,50,100,200,500")
    parser.add_argument("--gas-price-gwei", type=int, default=20)
    parser.add_argument("--amount", type=int, default=1_000_000)
    parser.add_argument("--message-delay-ms", type=int, default=40)
    parser.add_argument("--execution-delay-ms", type=int, default=20)
    parser.add_argument("--transport", default="xmtp", choices=["mock", "xmtp"])
    parser.add_argument("--xmtp-send-cmd", default="")
    parser.add_argument("--xmtp-recv-cmd", default="")
    parser.add_argument("--out", default="raw_data/tps.csv")
    parser.add_argument("--summary", default="raw_data/tps_summary.csv")
    parser.add_argument("--artifacts-dir", default="evm/out")
    parser.add_argument("--config", default="")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument(
        "--mnemonic",
        default="test test test test test test test test test test test junk",
    )
    return parser.parse_args()


def load_artifact(artifacts_dir: Path, contract_name: str):
    matches = list(artifacts_dir.glob(f"**/{contract_name}.sol/{contract_name}.json"))
    if not matches:
        matches = list(artifacts_dir.glob(f"**/{contract_name}.json"))
    if not matches:
        raise FileNotFoundError(
            f"找不到 {contract_name} 产物，请先在 evm/ 运行 forge build"
        )
    artifact_path = matches[0]
    with artifact_path.open("r", encoding="utf-8") as handle:
        artifact = json.load(handle)
    bytecode = artifact.get("bytecode")
    if isinstance(bytecode, dict):
        bytecode = bytecode.get("object")
    if not bytecode:
        raise ValueError(f"{contract_name} 缺少 bytecode")
    return artifact["abi"], bytecode


def deploy_contract(w3, abi, bytecode, args, sender, gas_price):
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = contract.constructor(*args).transact(
        {"from": sender, "gasPrice": gas_price}
    )
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def xmtp_roundtrip(send_cmd: str, recv_cmd: str, payload: str) -> None:
    bridge_cmd = os.environ.get("XMTP_BRIDGE_CMD")
    if bridge_cmd:
        bridge = ensure_xmtp_bridge(bridge_cmd)
        bridge.roundtrip(payload)
        return
    if not send_cmd or not recv_cmd:
        raise SystemExit("缺少 XMTP 命令，请设置 --xmtp-send-cmd 和 --xmtp-recv-cmd")
    env = os.environ.copy()
    last_msg_path = None
    temp_file = tempfile.NamedTemporaryFile(prefix="xmtp_last_msg_", delete=False)
    try:
        last_msg_path = temp_file.name
    finally:
        temp_file.close()
    env["XMTP_LAST_MSG_PATH"] = last_msg_path
    try:
        subprocess.run(
            send_cmd, input=payload.encode("utf-8"), shell=True, check=True, env=env
        )
        subprocess.run(recv_cmd, shell=True, check=True, env=env)
    finally:
        if last_msg_path:
            try:
                os.remove(last_msg_path)
            except FileNotFoundError:
                pass


_XMTP_BRIDGE = None
_XMTP_BRIDGE_LOCK = threading.Lock()


class XmtpBridge:
    def __init__(self, cmd: str):
        self.cmd = cmd
        self.proc = subprocess.Popen(
            cmd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if not self.proc.stdout:
            raise SystemExit("XMTP bridge 无法启动")
        start = time.time()
        last_lines = []
        while True:
            if self.proc.poll() is not None:
                tail = " | ".join(last_lines[-3:])
                raise SystemExit(f"XMTP bridge 启动失败: {tail}")
            line = self.proc.stdout.readline()
            if not line:
                if time.time() - start > 30:
                    tail = " | ".join(last_lines[-3:])
                    raise SystemExit(f"XMTP bridge 启动超时: {tail}")
                continue
            text = line.strip()
            if not text:
                continue
            if text.startswith("READY"):
                break
            last_lines.append(text)
            if text.startswith("ERROR:"):
                raise SystemExit(f"XMTP bridge 启动失败: {text}")

    def roundtrip(self, payload: str) -> None:
        if not self.proc.stdin or not self.proc.stdout:
            raise SystemExit("XMTP bridge 未就绪")
        safe_payload = payload.replace("\n", " ")
        self.proc.stdin.write(safe_payload + "\n")
        self.proc.stdin.flush()
        response = self.proc.stdout.readline().strip()
        if response.startswith("ERROR:"):
            raise SystemExit(response)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


def ensure_xmtp_bridge(cmd: str) -> XmtpBridge:
    global _XMTP_BRIDGE
    if _XMTP_BRIDGE is None:
        with _XMTP_BRIDGE_LOCK:
            if _XMTP_BRIDGE is None:
                _XMTP_BRIDGE = XmtpBridge(cmd)
    return _XMTP_BRIDGE


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mock_cid(payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()[:32]
    return f"mock-{digest}"


def hex_to_bytes(value: str) -> bytes:
    if value.startswith("0x"):
        value = value[2:]
    return bytes.fromhex(value)


def build_provenance(sa_account, exec_log: dict) -> dict:
    exec_log_hash = hashlib.sha256(canonical_json(exec_log).encode("utf-8")).digest()
    signature = sa_account.sign_message(encode_defunct(exec_log_hash)).signature
    return {
        "exec_log_hash": exec_log_hash.hex(),
        "signature": signature.hex(),
    }


def get_indexed_id(receipt, contract_address, topic0):
    for log in receipt["logs"]:
        if log["address"].lower() != contract_address.lower():
            continue
        if log["topics"][0] != topic0:
            continue
        return int.from_bytes(log["topics"][1], "big")
    return None


def sign_authorization(
    account,
    chain_id,
    token_address,
    owner,
    spender,
    value,
    valid_after,
    valid_before,
    nonce,
):
    if isinstance(nonce, bytearray):
        nonce = bytes(nonce)
    if isinstance(nonce, bytes) or (
        not isinstance(nonce, str) and hasattr(nonce, "hex")
    ):
        nonce_value = Web3.to_hex(nonce)
    elif isinstance(nonce, str):
        nonce_value = nonce if nonce.startswith("0x") else f"0x{nonce}"
    else:
        nonce_value = f"0x{int(nonce):064x}"
    if nonce_value.startswith("0x0x"):
        nonce_value = "0x" + nonce_value[4:]
    authorization = {
        "from": owner,
        "to": spender,
        "value": int(value),
        "validAfter": int(valid_after),
        "validBefore": int(valid_before),
        "nonce": nonce_value,
    }
    typed_data = x402.build_eip3009_typed_data(
        chain_id, token_address, authorization, name="MockUSDC", version="1"
    )
    signature = account.sign_message(encode_structured_data(typed_data))
    return signature.v, signature.r.to_bytes(32, "big"), signature.s.to_bytes(32, "big")


def percentile(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1


async def run_session(
    session_id,
    w3,
    service,
    pair,
    token_address,
    session_locked_topic,
    gas_price,
    amount,
    message_delay,
    execution_delay,
    stats,
    transport,
    xmtp_send_cmd,
    xmtp_recv_cmd,
    payment_url,
    network_id,
    sa_nonce_lock=None,
    sa_nonce_state=None,
):
    ua = pair["ua"]
    sa = pair["sa"]
    ua_account = pair["ua_account"]
    sa_account = pair["sa_account"]
    if transport == "mock":
        await asyncio.sleep(message_delay)
        stats["message_count"] += 1
        await asyncio.sleep(message_delay)
        stats["message_count"] += 1
    else:
        await asyncio.to_thread(
            xmtp_roundtrip,
            xmtp_send_cmd,
            xmtp_recv_cmd,
            f"request:{session_id}",
        )
        stats["message_count"] += 2

    request_payload = {
        "task_id": f"tps-{session_id}",
        "model_id": "model-v1",
        "prompt": "throughput",
        "negative_prompt": "none",
        "width": 512,
        "height": 512,
        "steps": 1,
        "seed": 1,
        "guidance_scale": 1,
        "sampler": 0,
        "deadline": 1700000000,
        "metadata_uri": "ipfs://placeholder",
    }
    request_hash = x402.request_hash(request_payload)
    challenge = x402_http.request_payment_challenge(payment_url, request_payload)
    accept = challenge["accepts"][0]
    expiry = int(time.time()) + 365 * 24 * 3600
    valid_after = 0
    valid_before = int(time.time()) + 365 * 24 * 3600
    auth_nonce = w3.keccak(text=f"auth-{session_id}-{time.time_ns()}")
    v, r, s = sign_authorization(
        ua_account,
        w3.eth.chain_id,
        token_address,
        ua,
        accept["payTo"],
        amount,
        valid_after,
        valid_before,
        auth_nonce,
    )
    signature_hex = "0x" + r.hex() + s.hex() + format(v, "02x")
    authorization = {
        "from": ua,
        "to": accept["payTo"],
        "value": str(accept["maxAmountRequired"]),
        "validAfter": str(valid_after),
        "validBefore": str(valid_before),
        "nonce": auth_nonce.hex(),
    }
    payment_payload = {
        "x402Version": 1,
        "scheme": accept["scheme"],
        "network": accept["network"],
        "payload": {
            "signature": signature_hex,
            "authorization": authorization,
        },
    }
    x_payment_header = x402.encode_x_payment(payment_payload)
    payment_result = await asyncio.to_thread(
        x402_http.send_payment, payment_url, request_payload, x_payment_header
    )
    if payment_result["status"] != 200:
        return
    payment_response = payment_result.get("payment_response") or {}
    deposit_tx_hash = payment_response.get("transaction")
    if not deposit_tx_hash:
        return
    receipt = await asyncio.to_thread(
        w3.eth.wait_for_transaction_receipt, deposit_tx_hash
    )
    stats["settlement_count"] += 1
    real_session_id = get_indexed_id(receipt, service.address, session_locked_topic)
    if real_session_id is None:
        return

    exec_started_at = time.time()
    await asyncio.sleep(execution_delay)
    exec_finished_at = time.time()
    quote_id = x402.quote_id(
        w3,
        request_hash,
        amount,
        token_address,
        sa,
        int(authorization["validBefore"]),
        hex_to_bytes(authorization["nonce"]),
    )
    proof_hash = quote_id
    message_hash = Web3.solidity_keccak(
        ["uint256", "bytes32"], [real_session_id, proof_hash]
    )
    signature = sa_account.sign_message(encode_defunct(message_hash)).signature
    nonce = None
    if sa_nonce_lock is not None and sa_nonce_state is not None:
        async with sa_nonce_lock:
            nonce = sa_nonce_state["value"]
            sa_nonce_state["value"] += 1
    tx_params = {"from": sa, "gasPrice": gas_price}
    if nonce is not None:
        tx_params["nonce"] = nonce
    settle_tx_hash = await asyncio.to_thread(
        service.functions.submitProofAndRelease(
            real_session_id, proof_hash, signature
        ).transact,
        tx_params,
    )
    await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, settle_tx_hash)
    stats["settlement_count"] += 1
    payload = b"{}"
    cid = mock_cid(payload)
    exec_log = {
        "request_hash": request_hash.hex(),
        "session_id": real_session_id,
        "receipt_tx_hash": receipt["transactionHash"].hex(),
        "settlement_tx_hash": settle_tx_hash.hex(),
        "cid": cid,
        "payload_bytes": len(payload),
        "exec_started_at": exec_started_at,
        "exec_finished_at": exec_finished_at,
    }
    provenance = build_provenance(sa_account, exec_log)
    if stats.get("provenance_sample") is None:
        stats["provenance_sample"] = {
            "cid": cid,
            "exec_log_hash": provenance["exec_log_hash"],
            "signature": provenance["signature"],
        }
    if transport == "mock":
        await asyncio.sleep(message_delay)
        stats["message_count"] += 1
    else:
        result_payload = json.dumps(
            {"cid": cid, "provenance": provenance},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await asyncio.to_thread(
            xmtp_roundtrip,
            xmtp_send_cmd,
            xmtp_recv_cmd,
            result_payload,
        )
        stats["message_count"] += 1
    stats["completed"] += 1


async def run_benchmark(
    w3,
    service,
    pairs,
    token_address,
    session_locked_topic,
    gas_price,
    amount,
    message_delay,
    execution_delay,
    duration,
    warmup,
    concurrency,
    transport,
    xmtp_send_cmd,
    xmtp_recv_cmd,
    payment_url,
    network_id,
):
    stats = {
        "message_count": 0.0,
        "settlement_count": 0.0,
        "completed": 0.0,
        "provenance_sample": None,
    }
    start = time.perf_counter()
    end_time = start + duration
    sa_nonce_lock = asyncio.Lock()
    sa_address = pairs[0]["sa"] if pairs else None
    sa_nonce_state = None
    if sa_address:
        sa_nonce_state = {
            "value": w3.eth.get_transaction_count(sa_address, "pending")
        }

    async def worker(worker_id):
        counter = 0
        while time.perf_counter() < end_time:
            pair = pairs[worker_id % len(pairs)]
            await run_session(
                f"{worker_id}-{counter}",
                w3,
                service,
                pair,
                token_address,
                session_locked_topic,
                gas_price,
                amount,
                message_delay,
                execution_delay,
                stats,
                transport,
                xmtp_send_cmd,
                xmtp_recv_cmd,
                payment_url,
                network_id,
                sa_nonce_lock=sa_nonce_lock,
                sa_nonce_state=sa_nonce_state,
            )
            counter += 1

    tasks = [asyncio.create_task(worker(i)) for i in range(concurrency)]
    await asyncio.sleep(warmup)
    warmup_end = time.perf_counter()
    base_message = stats["message_count"]
    base_settlement = stats["settlement_count"]
    base_completed = stats["completed"]
    await asyncio.gather(*tasks)
    total_time = time.perf_counter() - warmup_end
    measured = {
        "message_count": max(stats["message_count"] - base_message, 0.0),
        "settlement_count": max(stats["settlement_count"] - base_settlement, 0.0),
        "completed": max(stats["completed"] - base_completed, 0.0),
    }
    provenance_sample = stats.get("provenance_sample") or {}
    return measured, total_time, provenance_sample


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts_dir = root / args.artifacts_dir
    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    config = None
    if args.config:
        config = fig3_config.load_config(args.config)
        fig3_config.apply_tps_config(args, config)

    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    concurrency_levels = [
        int(item) for item in args.concurrency.split(",") if item.strip()
    ]
    if not concurrency_levels:
        raise SystemExit("concurrency 不能为空")

    w3 = Web3(
        Web3.HTTPProvider(
            args.rpc_url,
            request_kwargs={"timeout": 30, "proxies": {"http": None, "https": None}},
        )
    )
    if not w3.is_connected():
        raise SystemExit("无法连接到 RPC，请确认 Anvil 已启动")

    accounts = w3.eth.accounts
    if len(accounts) < 3:
        raise SystemExit("RPC 账户数量不足，需要至少 3 个账户")

    deployer = accounts[0]
    sa_addr = accounts[1]
    ua_addresses = accounts[2:]
    if not ua_addresses:
        raise SystemExit("可用 UA 账户不足，请增加 Anvil accounts")
    gas_price = w3.to_wei(args.gas_price_gwei, "gwei")

    Account.enable_unaudited_hdwallet_features()
    if max(concurrency_levels) > len(ua_addresses):
        raise SystemExit(
            f"并发 {max(concurrency_levels)} 超过可用 UA 数量 {len(ua_addresses)}，请使用 anvil --accounts 增加账户数"
        )

    sa_account = Account.from_mnemonic(
        args.mnemonic, account_path="m/44'/60'/0'/0/1"
    )
    if sa_account.address.lower() != sa_addr.lower():
        raise SystemExit("SA 地址与 RPC 账户不一致")

    pairs = []
    for index, ua_addr in enumerate(ua_addresses, start=2):
        ua_account = Account.from_mnemonic(
            args.mnemonic, account_path=f"m/44'/60'/0'/0/{index}"
        )
        if ua_account.address.lower() != ua_addr.lower():
            raise SystemExit("UA 地址与 RPC 账户不一致")
        pairs.append(
            {
                "ua": ua_addr,
                "sa": sa_addr,
                "ua_account": ua_account,
                "sa_account": sa_account,
            }
        )

    token_abi, token_bytecode = load_artifact(artifacts_dir, "MockUSDC")
    service_abi, service_bytecode = load_artifact(artifacts_dir, "ServiceEscrow")

    token = deploy_contract(w3, token_abi, token_bytecode, [], deployer, gas_price)
    service = deploy_contract(
        w3, service_abi, service_bytecode, [token.address], deployer, gas_price
    )

    session_locked_topic = w3.keccak(
        text="SessionLocked(uint256,address,address,uint256,bytes32,bytes32,uint256)"
    )

    network_id = f"anvil-{w3.eth.chain_id}"

    def build_accepts(payload, resource):
        return {
            "x402Version": 1,
            "accepts": [
                {
                    "scheme": "exact",
                    "network": network_id,
                    "maxAmountRequired": str(args.amount),
                    "resource": resource,
                    "description": "Agent-OSI throughput experiment",
                    "payTo": service.address,
                    "asset": token.address,
                    "maxTimeoutSeconds": 60,
                }
            ],
        }

    def decode_signature(sig_hex: str):
        return x402.split_signature(sig_hex)

    def execute_payment(request_payload, payment_payload, accept):
        inner = payment_payload["payload"]
        authorization = inner["authorization"]
        signature = inner["signature"]
        payer = authorization["from"]
        valid_after = int(authorization["validAfter"])
        valid_before = int(authorization["validBefore"])
        nonce = hex_to_bytes(authorization["nonce"])
        v, r, s = decode_signature(signature)
        request_hash = x402.request_hash(request_payload)
        quote_id = x402.quote_id(
            w3,
            request_hash,
            args.amount,
            token.address,
            sa_addr,
            valid_before,
            nonce,
        )
        tx_hash = service.functions.depositLockWithAuthorization(
            payer,
            sa_addr,
            request_hash,
            args.amount,
            quote_id,
            valid_before,
            valid_after,
            valid_before,
            nonce,
            v,
            r,
            s,
        ).transact({"from": deployer, "gasPrice": gas_price})
        return {"transaction": tx_hash.hex(), "quote_id": quote_id.hex()}

    server = x402_http.X402Server(
        accepts_builder=build_accepts,
        payment_executor=execute_payment,
        chain_id=w3.eth.chain_id,
        token_name="MockUSDC",
        token_version="1",
    )
    server.start()
    payment_url = server.url

    if args.transport == "xmtp" and os.environ.get("XMTP_BRIDGE_CMD"):
        ensure_xmtp_bridge(os.environ["XMTP_BRIDGE_CMD"])

    if args.transport == "xmtp":
        if not os.environ.get("XMTP_BRIDGE_CMD") and (
            not args.xmtp_send_cmd or not args.xmtp_recv_cmd
        ):
            raise SystemExit("缺少 XMTP 命令，请设置 --xmtp-send-cmd 和 --xmtp-recv-cmd")

    total_needed = args.amount * args.duration * max(args.trials, 1) * 100
    for pair in pairs:
        mint_tx = token.functions.mint(pair["ua"], total_needed).transact(
            {"from": deployer, "gasPrice": gas_price}
        )
        w3.eth.wait_for_transaction_receipt(mint_tx)

    rows = []
    try:
        for concurrency in concurrency_levels:
            for trial in range(args.trials):
                stats, elapsed, provenance_sample = asyncio.run(
                    run_benchmark(
                        w3,
                        service,
                        pairs,
                        token.address,
                        session_locked_topic,
                        gas_price,
                        args.amount,
                        args.message_delay_ms / 1000,
                        args.execution_delay_ms / 1000,
                        args.duration,
                        args.warmup,
                        concurrency,
                        args.transport,
                        args.xmtp_send_cmd,
                        args.xmtp_recv_cmd,
                        payment_url,
                        network_id,
                    )
                )
                rows.append(
                    {
                        "concurrency": concurrency,
                        "trial": trial,
                        "duration": elapsed,
                        "message_throughput": stats["message_count"] / elapsed,
                        "settlement_throughput": stats["settlement_count"] / elapsed,
                        "completed_throughput": stats["completed"] / elapsed,
                        "provenance_sample_cid": provenance_sample.get("cid", ""),
                        "provenance_sample_exec_log_hash": provenance_sample.get(
                            "exec_log_hash", ""
                        ),
                        "provenance_sample_signature": provenance_sample.get(
                            "signature", ""
                        ),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
    finally:
        if _XMTP_BRIDGE is not None:
            _XMTP_BRIDGE.close()

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "concurrency",
                "trial",
                "duration",
                "message_throughput",
                "settlement_throughput",
                "completed_throughput",
                "provenance_sample_cid",
                "provenance_sample_exec_log_hash",
                "provenance_sample_signature",
                "timestamp",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = {}
    for row in rows:
        summary.setdefault(
            row["concurrency"], {"message": [], "settlement": [], "completed": []}
        )
        summary[row["concurrency"]]["message"].append(row["message_throughput"])
        summary[row["concurrency"]]["settlement"].append(row["settlement_throughput"])
        summary[row["concurrency"]]["completed"].append(row["completed_throughput"])

    summary_rows = []
    for concurrency, values in summary.items():
        summary_rows.append(
            {
                "concurrency": concurrency,
                "n": len(values["message"]),
                "message_median": percentile(values["message"], 0.5),
                "message_p10": percentile(values["message"], 0.1),
                "message_p90": percentile(values["message"], 0.9),
                "settlement_median": percentile(values["settlement"], 0.5),
                "settlement_p10": percentile(values["settlement"], 0.1),
                "settlement_p90": percentile(values["settlement"], 0.9),
                "completed_median": percentile(values["completed"], 0.5),
                "completed_p10": percentile(values["completed"], 0.1),
                "completed_p90": percentile(values["completed"], 0.9),
            }
        )

    summary_path = root / args.summary
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "concurrency",
                "n",
                "message_median",
                "message_p10",
                "message_p90",
                "settlement_median",
                "settlement_p10",
                "settlement_p90",
                "completed_median",
                "completed_p10",
                "completed_p90",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    experiment_id = fig3_env.resolve_experiment_id(args.experiment_id)
    config_snapshot = fig3_config.build_tps_config_snapshot(args, config)
    dependencies = {
        "anvil": {
            "rpc_url": args.rpc_url,
            "connected": w3.is_connected(),
            "chain_id": w3.eth.chain_id,
        },
        "xmtp": {
            "transport": args.transport,
            "enabled": args.transport == "xmtp",
            "send_cmd": args.xmtp_send_cmd,
            "recv_cmd": args.xmtp_recv_cmd,
        },
    }
    extra = {
        "rpc_url": args.rpc_url,
        "samples": args.trials,
        "concurrency": concurrency_levels,
        "duration": args.duration,
        "warmup": args.warmup,
        "transport": args.transport,
        "message_delay_ms": args.message_delay_ms,
        "execution_delay_ms": args.execution_delay_ms,
        "workload": "light",
        "provenance_enabled": True,
        "fixed_sa": sa_addr,
        "ua_count": len(pairs),
        "concurrency_definition": "UA_to_fixed_SA",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    env_payload = fig3_env.build_env(
        experiment_id=experiment_id,
        script="collect_tps.py",
        config=config_snapshot,
        dependencies=dependencies,
        extra=extra,
        root_dir=root,
    )
    fig3_env.write_env(root / "raw_data" / "fig3c_env.json", env_payload)
    fig3_env.write_env(root / "raw_data" / "tps_env.json", env_payload)

    server.stop()


if __name__ == "__main__":
    main()
