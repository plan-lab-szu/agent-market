#!/usr/bin/env python3
import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct, encode_structured_data
from web3 import Web3

import fig3_config
import fig3_env
import x402
import x402_http


DEFAULT_MNEMONIC = "test test test test test test test test test test test junk"


def parse_args():
    parser = argparse.ArgumentParser(description="采集 Fig.3(a) 成本数据")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--gas-price-gwei", type=int, default=20)
    parser.add_argument("--amount", type=int, default=1_000_000)
    parser.add_argument("--out", default="raw_data/costs.csv")
    parser.add_argument("--artifacts-dir", default="evm/out")
    parser.add_argument("--mnemonic", default=DEFAULT_MNEMONIC)
    parser.add_argument("--config", default="")
    parser.add_argument("--experiment-id", default="")
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


def to_bytes32_array(text: str, slots: int):
    data = text.encode("utf-8")
    max_len = 32 * slots
    if len(data) > max_len:
        data = data[:max_len]
    padded = data.ljust(max_len, b"\0")
    return tuple(padded[i : i + 32] for i in range(0, max_len, 32))




def get_block_timestamp(w3, receipt):
    block = w3.eth.get_block(receipt.blockNumber)
    return block["timestamp"]


def record_row(
    rows,
    run_id,
    architecture,
    phase,
    receipt,
    gas_price_gwei,
    w3,
    gas_override=None,
):
    gas_used = receipt["gasUsed"] if gas_override is None else gas_override
    rows.append(
        {
            "run_id": run_id,
            "architecture": architecture,
            "phase": phase,
            "gas_used": gas_used,
            "gas_price_gwei": gas_price_gwei,
            "tx_hash": receipt["transactionHash"].hex(),
            "timestamp": get_block_timestamp(w3, receipt),
        }
    )


def sign_proof(account, session_id, proof_hash):
    message_hash = Web3.solidity_keccak(
        ["uint256", "bytes32"], [session_id, proof_hash]
    )
    return account.sign_message(encode_defunct(message_hash)).signature


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


def hex_to_bytes(value: str) -> bytes:
    if value.startswith("0x"):
        value = value[2:]
    return bytes.fromhex(value)


def get_indexed_id(receipt, contract_address, topic0):
    for log in receipt["logs"]:
        if log["address"].lower() != contract_address.lower():
            continue
        if log["topics"][0] != topic0:
            continue
        return int.from_bytes(log["topics"][1], "big")
    return None


def require_event_id(receipt, contract_address, topic0, label):
    if receipt["status"] != 1:
        raise SystemExit(f"{label} 交易失败: {receipt['transactionHash'].hex()}")
    event_id = get_indexed_id(receipt, contract_address, topic0)
    if event_id is None:
        raise SystemExit(f"{label} 事件缺失: {receipt['transactionHash'].hex()}")
    return event_id


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts_dir = root / args.artifacts_dir
    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    config = None
    if args.config:
        config = fig3_config.load_config(args.config)
        fig3_config.apply_costs_config(args, config)

    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    if args.samples < 100:
        print("警告：samples 小于 100，结果不符合论文统计口径")

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
    ua = accounts[1]
    sa = accounts[2]
    gas_price = w3.to_wei(args.gas_price_gwei, "gwei")

    Account.enable_unaudited_hdwallet_features()
    sa_account = Account.from_mnemonic(args.mnemonic, account_path="m/44'/60'/0'/0/2")
    if sa_account.address.lower() != sa.lower():
        raise SystemExit("派生的 SA 地址与 RPC 账户不一致，请检查 mnemonic")
    ua_account = Account.from_mnemonic(args.mnemonic, account_path="m/44'/60'/0'/0/1")
    if ua_account.address.lower() != ua.lower():
        raise SystemExit("派生的 UA 地址与 RPC 账户不一致，请检查 mnemonic")

    identity_abi, identity_bytecode = load_artifact(artifacts_dir, "IdentityRegistry")
    token_abi, token_bytecode = load_artifact(artifacts_dir, "MockUSDC")
    service_abi, service_bytecode = load_artifact(artifacts_dir, "ServiceEscrow")
    order_abi, order_bytecode = load_artifact(artifacts_dir, "OrderAnchoredEscrow")
    hook_abi, hook_bytecode = load_artifact(artifacts_dir, "SettlementHook")

    identity = deploy_contract(
        w3, identity_abi, identity_bytecode, [], deployer, gas_price
    )
    token = deploy_contract(w3, token_abi, token_bytecode, [], deployer, gas_price)
    service = deploy_contract(
        w3, service_abi, service_bytecode, [token.address], deployer, gas_price
    )
    order = deploy_contract(
        w3, order_abi, order_bytecode, [token.address], deployer, gas_price
    )
    hook = deploy_contract(w3, hook_abi, hook_bytecode, [], deployer, gas_price)
    session_locked_topic = w3.keccak(
        text="SessionLocked(uint256,address,address,uint256,bytes32,bytes32,uint256)"
    )
    order_locked_topic = w3.keccak(
        text="OrderLocked(uint256,address,address,uint256,bytes32,bytes32,uint256)"
    )

    total_sessions = args.samples * 2
    total_needed = args.amount * (total_sessions + 10)
    mint_tx = token.functions.mint(ua, total_needed).transact(
        {"from": deployer, "gasPrice": gas_price}
    )
    w3.eth.wait_for_transaction_receipt(mint_tx)
    approve_tx = token.functions.approve(order.address, total_needed).transact(
        {"from": ua, "gasPrice": gas_price}
    )
    w3.eth.wait_for_transaction_receipt(approve_tx)
    approve_tx = token.functions.approve(service.address, total_needed).transact(
        {"from": ua, "gasPrice": gas_price}
    )
    w3.eth.wait_for_transaction_receipt(approve_tx)
    approve_tx = token.functions.approve(order.address, total_needed).transact(
        {"from": ua, "gasPrice": gas_price}
    )
    w3.eth.wait_for_transaction_receipt(approve_tx)
    permit_deadline = int(time.time()) + 365 * 24 * 3600

    prompt = to_bytes32_array("cyberpunk cat", 8)
    negative_prompt = to_bytes32_array("low quality", 4)
    metadata_uri = to_bytes32_array("ipfs://placeholder", 2)

    def build_x_payment(accept, payer, payer_account):
        valid_after = 0
        valid_before = permit_deadline
        auth_nonce = w3.keccak(text=f"auth-{payer}-{time.time_ns()}")
        v, r, s = sign_authorization(
            payer_account,
            w3.eth.chain_id,
            token.address,
            payer,
            accept["payTo"],
            int(accept["maxAmountRequired"]),
            valid_after,
            valid_before,
            auth_nonce,
        )
        signature_hex = "0x" + r.hex() + s.hex() + format(v, "02x")
        authorization = {
            "from": payer,
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
        return x402.encode_x_payment(payment_payload), authorization

    rows = []
    run_id = 0

    network_id = f"anvil-{w3.eth.chain_id}"

    def build_accepts_for(pay_to: str):
        def _builder(payload, resource):
            return {
                "x402Version": 1,
                "accepts": [
                    {
                        "scheme": "exact",
                        "network": network_id,
                        "maxAmountRequired": str(args.amount),
                        "resource": resource,
                        "description": "Agent-OSI cost experiment",
                        "payTo": pay_to,
                        "asset": token.address,
                        "maxTimeoutSeconds": 60,
                    }
                ],
            }

        return _builder

    def decode_signature(sig_hex: str):
        return x402.split_signature(sig_hex)

    def execute_agent_payment(request_payload, payment_payload, accept):
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
            sa,
            valid_before,
            nonce,
        )
        tx_hash = service.functions.depositLockWithAuthorization(
            payer,
            sa,
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

    def execute_web3_payment(request_payload, payment_payload, accept):
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
            sa,
            valid_before,
            nonce,
        )
        order_input = (
            w3.keccak(text=request_payload["task_id"]),
            w3.keccak(text=request_payload["model_id"]),
            request_hash,
            prompt,
            negative_prompt,
            request_payload["width"],
            request_payload["height"],
            request_payload["steps"],
            request_payload["seed"],
            request_payload["guidance_scale"],
            request_payload["sampler"],
            request_payload["deadline"],
            metadata_uri,
        )
        tx_hash = order.functions.createOrderAndLockWithAuthorization(
            payer,
            sa,
            args.amount,
            order_input,
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

    server_agent = x402_http.X402Server(
        accepts_builder=build_accepts_for(service.address),
        payment_executor=execute_agent_payment,
        chain_id=w3.eth.chain_id,
        token_name="MockUSDC",
        token_version="1",
    )
    server_web3 = x402_http.X402Server(
        accepts_builder=build_accepts_for(order.address),
        payment_executor=execute_web3_payment,
        chain_id=w3.eth.chain_id,
        token_name="MockUSDC",
        token_version="1",
    )
    server_agent.start()
    server_web3.start()
    base_url_agent = server_agent.url
    base_url_web3 = server_web3.url

    for sample in range(args.samples):
        did_hash = w3.keccak(text=f"did-web3-{sample}")
        tx_hash = identity.functions.register(did_hash).transact(
            {"from": ua, "gasPrice": gas_price}
        )
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        record_row(
            rows,
            run_id,
            "web3_conventional",
            "identity",
            receipt,
            args.gas_price_gwei,
            w3,
        )
        run_id += 1

        did_hash = w3.keccak(text=f"did-agent-{sample}")
        tx_hash = identity.functions.register(did_hash).transact(
            {"from": ua, "gasPrice": gas_price}
        )
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        record_row(
            rows,
            run_id,
            "agent_osi_single",
            "identity",
            receipt,
            args.gas_price_gwei,
            w3,
        )
        run_id += 1

    for sample in range(args.samples):
        task_id = w3.keccak(text=f"task-{sample}")
        request_payload = {
            "task_id": f"task-{sample}",
            "model_id": "model-v1",
            "prompt": "cyberpunk cat",
            "negative_prompt": "low quality",
            "width": 512,
            "height": 512,
            "steps": 30,
            "seed": 42,
            "guidance_scale": 750,
            "sampler": 1,
            "deadline": 1700000000,
            "metadata_uri": "ipfs://placeholder",
        }
        challenge = x402_http.request_payment_challenge(base_url_web3, request_payload)
        accept = challenge["accepts"][0]
        x_payment_header, authorization = build_x_payment(accept, ua, ua_account)
        payment_result = x402_http.send_payment(
            base_url_web3, request_payload, x_payment_header
        )
        if payment_result["status"] != 200:
            raise SystemExit(f"X-PAYMENT 失败: {payment_result.get('body')}")
        payment_response = payment_result.get("payment_response") or {}
        tx_hash = payment_response.get("transaction")
        if not tx_hash:
            raise SystemExit("缺少 X-PAYMENT-RESPONSE transaction")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        record_row(
            rows,
            run_id,
            "web3_conventional",
            "session",
            receipt,
            args.gas_price_gwei,
            w3,
        )
        order_id = require_event_id(
            receipt, order.address, order_locked_topic, "Web3 Order"
        )
        request_hash = x402.request_hash(request_payload)
        quote_id = x402.quote_id(
            w3,
            request_hash,
            args.amount,
            token.address,
            sa,
            int(authorization["validBefore"]),
            hex_to_bytes(authorization["nonce"]),
        )
        run_id += 1
        proof_hash = w3.solidity_keccak(
            ["bytes32", "bytes32"], [request_hash, quote_id]
        )
        signature = sign_proof(sa_account, order_id, proof_hash)
        tx_hash = order.functions.settleOrder(
            order_id, proof_hash, signature
        ).transact({"from": sa, "gasPrice": gas_price})
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        record_row(
            rows,
            run_id,
            "web3_conventional",
            "settlement",
            receipt,
            args.gas_price_gwei,
            w3,
        )
        run_id += 1

    for sample in range(args.samples):
        request_payload = {
            "task_id": f"task-{sample}",
            "model_id": "model-v1",
            "prompt": "cyberpunk cat",
            "negative_prompt": "low quality",
            "width": 512,
            "height": 512,
            "steps": 30,
            "seed": 42,
            "guidance_scale": 750,
            "sampler": 1,
            "deadline": 1700000000,
            "metadata_uri": "ipfs://placeholder",
        }
        challenge = x402_http.request_payment_challenge(base_url_agent, request_payload)
        accept = challenge["accepts"][0]
        x_payment_header, authorization = build_x_payment(accept, ua, ua_account)
        payment_result = x402_http.send_payment(
            base_url_agent, request_payload, x_payment_header
        )
        if payment_result["status"] != 200:
            raise SystemExit(f"X-PAYMENT 失败: {payment_result.get('body')}")
        payment_response = payment_result.get("payment_response") or {}
        tx_hash = payment_response.get("transaction")
        if not tx_hash:
            raise SystemExit("缺少 X-PAYMENT-RESPONSE transaction")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        record_row(
            rows,
            run_id,
            "agent_osi_single",
            "session",
            receipt,
            args.gas_price_gwei,
            w3,
        )
        run_id += 1
        session_id = require_event_id(
            receipt, service.address, session_locked_topic, "Agent Session"
        )
        request_hash = x402.request_hash(request_payload)
        quote_id = x402.quote_id(
            w3,
            request_hash,
            args.amount,
            token.address,
            sa,
            int(authorization["validBefore"]),
            hex_to_bytes(authorization["nonce"]),
        )
        proof_hash = quote_id
        signature = sign_proof(sa_account, session_id, proof_hash)
        tx_hash = service.functions.submitProofAndRelease(
            session_id, proof_hash, signature
        ).transact({"from": sa, "gasPrice": gas_price})
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        record_row(
            rows,
            run_id,
            "agent_osi_single",
            "settlement",
            receipt,
            args.gas_price_gwei,
            w3,
        )
        run_id += 1

    server_agent.stop()
    server_web3.stop()

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "architecture",
                "phase",
                "gas_used",
                "gas_price_gwei",
                "tx_hash",
                "timestamp",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    experiment_id = fig3_env.resolve_experiment_id(args.experiment_id)
    config_snapshot = fig3_config.build_costs_config_snapshot(args, config)
    dependencies = {
        "anvil": {
            "rpc_url": args.rpc_url,
            "connected": w3.is_connected(),
            "chain_id": w3.eth.chain_id,
        }
    }
    extra = {
        "rpc_url": args.rpc_url,
        "chain_id": w3.eth.chain_id,
        "gas_price_gwei": args.gas_price_gwei,
        "samples": args.samples,
        "amount": args.amount,
        "contracts": {
            "identity_registry": identity.address,
            "service_escrow": service.address,
            "settlement_hook": hook.address,
            "order_anchored_escrow": order.address,
            "token": token.address,
        },
        "accounts": {
            "deployer": deployer,
            "ua": ua,
            "sa": sa,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    env_payload = fig3_env.build_env(
        experiment_id=experiment_id,
        script="collect_costs.py",
        config=config_snapshot,
        dependencies=dependencies,
        extra=extra,
        root_dir=root,
    )
    fig3_env.write_env(out_path.parent / "fig3a_env.json", env_payload)
    fig3_env.write_env(out_path.parent / "costs_env.json", env_payload)


if __name__ == "__main__":
    main()
