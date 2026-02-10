#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import math
import os
import random
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse, request

from eth_account import Account
from eth_account.messages import encode_defunct, encode_structured_data
from web3 import Web3

import fig3_config
import fig3_env
import x402
import x402_http


def parse_args():
    parser = argparse.ArgumentParser(description="采集 Fig.3(b) 延迟数据")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--gas-price-gwei", type=int, default=20)
    parser.add_argument("--amount", type=int, default=1_000_000)
    parser.add_argument(
        "--workload", default="all", choices=["all", "light", "pipeline", "genai"]
    )
    parser.add_argument("--ipfs-api", default="http://127.0.0.1:5001")
    parser.add_argument("--ipfs-mode", default=None, choices=["auto", "real", "mock"])
    parser.add_argument("--message-delay-ms", type=int, default=40)
    parser.add_argument("--transport", default="xmtp", choices=["mock", "xmtp"])
    parser.add_argument("--xmtp-send-cmd", default="")
    parser.add_argument("--xmtp-recv-cmd", default="")
    parser.add_argument("--pipeline-steps", type=int, default=5)
    parser.add_argument("--pipeline-bytes", type=int, default=256 * 1024)
    parser.add_argument("--genai-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--genai-delay-min", type=float, default=5.0)
    parser.add_argument("--genai-delay-max", type=float, default=15.0)
    parser.add_argument(
        "--genai-mode", default="auto", choices=["auto", "real", "mock"]
    )
    parser.add_argument(
        "--genai-output-dir", default="outputs/fig3_genai_images"
    )
    parser.add_argument("--image-width", type=int, default=1024)
    parser.add_argument("--image-height", type=int, default=1024)
    parser.add_argument("--image-steps", type=int, default=30)
    parser.add_argument("--image-seed", type=int, default=42)
    parser.add_argument("--comfyui-url", default="")
    parser.add_argument("--comfyui-workflow", default="")
    parser.add_argument("--comfyui-timeout", type=int, default=180)
    parser.add_argument("--out", default="raw_data/latency.csv")
    parser.add_argument("--artifacts-dir", default="evm/out")
    parser.add_argument("--config", default="")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument(
        "--mnemonic",
        default="test test test test test test test test test test test junk",
    )
    parser.add_argument("--summary", default="raw_data/latency_summary.csv")
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


def load_infra_env():
    root = Path(__file__).resolve().parents[1]
    env_path = root / "scripts" / "infrastructure" / "env.json"
    if not env_path.exists():
        return {}
    with env_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def simulate_message(delay_ms):
    time.sleep(delay_ms / 1000)


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
        _XMTP_BRIDGE = XmtpBridge(cmd)
    return _XMTP_BRIDGE


def ipfs_add(api_url: str, payload: bytes) -> str:
    boundary = "----agentosi"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="payload.bin"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        + payload
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    req = request.Request(
        f"{api_url}/api/v0/add",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with request.urlopen(req, timeout=30) as response:
        data = response.read().decode("utf-8")
    last_line = data.strip().split("\n")[-1]
    payload = json.loads(last_line)
    if isinstance(payload, dict):
        return payload.get("Hash", "")
    return ""


def mock_cid(payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()[:32]
    return f"mock-{digest}"


def build_light_payload() -> bytes:
    target_bytes = random.randint(1024, 5120)
    base = {"status": "ok", "result": ""}
    overhead = len(
        json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    filler_len = max(target_bytes - overhead, 0)
    base["result"] = "a" * filler_len
    return json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_provenance(sa_account, exec_log: dict) -> dict:
    exec_log_hash = hashlib.sha256(canonical_json(exec_log).encode("utf-8")).digest()
    signature = sa_account.sign_message(encode_defunct(exec_log_hash)).signature
    return {
        "exec_log_hash": exec_log_hash.hex(),
        "signature": signature.hex(),
    }


def load_comfyui_workflow(path: str):
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_comfyui_workflow(
    workflow: dict,
    *,
    seed: int,
    width: int,
    height: int,
    steps: int,
    filename_prefix: str,
) -> dict:
    cloned = json.loads(json.dumps(workflow))
    for node in cloned.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if class_type == "EmptyLatentImage":
            inputs["width"] = width
            inputs["height"] = height
        elif class_type == "KSampler":
            inputs["seed"] = seed
            inputs["steps"] = steps
        elif class_type == "SaveImage":
            inputs["filename_prefix"] = filename_prefix
    return cloned


REQUIRED_FIELDS = (
    "task_id",
    "model_id",
    "prompt",
    "negative_prompt",
    "width",
    "height",
    "steps",
    "seed",
    "guidance_scale",
    "sampler",
    "deadline",
    "metadata_uri",
)


def comfyui_request(base_url: str, endpoint: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def comfyui_fetch(base_url: str, endpoint: str):
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(f"{base_url}{endpoint}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def run_comfyui(base_url: str, workflow: dict, timeout: int):
    response = comfyui_request(base_url, "/prompt", {"prompt": workflow})
    prompt_id = response.get("prompt_id")
    if not prompt_id:
        raise SystemExit("ComfyUI 未返回 prompt_id")
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = comfyui_fetch(base_url, f"/history/{prompt_id}")
        if str(prompt_id) in history:
            return history[str(prompt_id)]
        time.sleep(1)
    raise SystemExit("ComfyUI 超时")


def _comfyui_image_endpoint(image: dict) -> str:
    filename = str(image.get("filename", ""))
    subfolder = str(image.get("subfolder", ""))
    image_type = str(image.get("type", "output"))
    query = parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": image_type}
    )
    return f"/view?{query}"


def save_comfyui_outputs(
    base_url: str, history: dict, output_dir: Path, prefix: str
) -> int:
    outputs = history.get("outputs", {}) if isinstance(history, dict) else {}
    if not isinstance(outputs, dict):
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for output in outputs.values():
        images = output.get("images") if isinstance(output, dict) else None
        if not isinstance(images, list):
            continue
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict):
                continue
            filename = str(image.get("filename", ""))
            if not filename:
                continue
            endpoint = _comfyui_image_endpoint(image)
            req = request.Request(f"{base_url}{endpoint}", method="GET")
            opener = request.build_opener(request.ProxyHandler({}))
            with opener.open(req, timeout=30) as response:
                content = response.read()
            safe_name = Path(filename).name
            target = output_dir / f"{prefix}_{index:04d}_{safe_name}"
            target.write_bytes(content)
            saved += 1
    return saved


def copy_local_comfyui_outputs(root: Path, output_dir: Path, prefix: str) -> int:
    source_dir = root / "ComfyUI" / "output"
    if not source_dir.exists():
        return 0
    matches = sorted(source_dir.glob(f"{prefix}*.png"))
    if not matches:
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in matches:
        target = output_dir / path.name
        if target.exists():
            continue
        target.write_bytes(path.read_bytes())
        copied += 1
    return copied


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


def validate_request_payload(payload: dict) -> None:
    missing = [key for key in REQUIRED_FIELDS if key not in payload]
    if missing:
        raise SystemExit(f"请求字段缺失: {', '.join(missing)}")
    if not isinstance(payload.get("width"), int) or not isinstance(
        payload.get("height"), int
    ):
        raise SystemExit("请求分辨率字段必须为整数")
    if not isinstance(payload.get("steps"), int) or not isinstance(
        payload.get("seed"), int
    ):
        raise SystemExit("请求 steps/seed 必须为整数")


def verify_request_hash(payload: dict, expected_hash: bytes) -> None:
    canonical = x402.canonical_json(payload)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    if digest != expected_hash:
        raise SystemExit("requestHash 与 payload 不一致")


def run_pipeline(payload: dict, request_hash: bytes, steps: int, signer) -> None:
    validate_request_payload(payload)
    verify_request_hash(payload, request_hash)
    current = request_hash
    for index in range(steps):
        step_hash = Web3.solidity_keccak(
            ["bytes32", "uint16"], [current, index]
        )
        signer.sign_message(encode_defunct(step_hash))
        current = step_hash


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


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts_dir = root / args.artifacts_dir
    out_path = root / args.out
    summary_path = root / args.summary
    out_path.parent.mkdir(parents=True, exist_ok=True)

    config = None
    workloads_override = None
    if args.config:
        config = fig3_config.load_config(args.config)
        workloads_override = fig3_config.apply_latency_config(args, config)
    if args.ipfs_mode is None:
        args.ipfs_mode = "real"

    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    infra_env = load_infra_env()
    comfyui_url = (
        args.comfyui_url
        or os.environ.get("COMFYUI_URL", "")
        or infra_env.get("comfyui_url", "")
    )
    comfyui_workflow_path = (
        args.comfyui_workflow
        or os.environ.get("COMFYUI_WORKFLOW", "")
        or infra_env.get("comfyui_workflow", "")
    )
    xmtp_send_cmd = (
        args.xmtp_send_cmd
        or os.environ.get("XMTP_SEND_CMD", "")
        or infra_env.get("xmtp_send_cmd", "")
    )
    xmtp_recv_cmd = (
        args.xmtp_recv_cmd
        or os.environ.get("XMTP_RECV_CMD", "")
        or infra_env.get("xmtp_recv_cmd", "")
    )
    comfyui_workflow = load_comfyui_workflow(comfyui_workflow_path)
    use_comfyui = bool(comfyui_url and comfyui_workflow)

    genai_mode = args.genai_mode
    if genai_mode == "auto":
        genai_mode = "real" if use_comfyui else "mock"
    elif genai_mode == "real" and not use_comfyui:
        raise SystemExit("GenAI 负载要求真实 ComfyUI，但未配置")
    elif genai_mode == "mock":
        use_comfyui = False
    args.genai_mode = genai_mode

    if workloads_override is not None:
        workloads = workloads_override
    else:
        workloads = (
            ["light", "pipeline", "genai"] if args.workload == "all" else [args.workload]
        )

    experiment_id = fig3_env.resolve_experiment_id(args.experiment_id)
    genai_output_dir = root / args.genai_output_dir

    if args.transport == "xmtp":
        if not os.environ.get("XMTP_BRIDGE_CMD") and (
            not xmtp_send_cmd or not xmtp_recv_cmd
        ):
            raise SystemExit(
                "缺少 XMTP 命令，请设置 --xmtp-send-cmd 和 --xmtp-recv-cmd"
            )

    if "genai" in workloads and genai_mode == "mock":
        print("提示：GenAI 使用 mock 模式，延迟由配置的随机区间模拟")

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
    ua_account = Account.from_mnemonic(args.mnemonic, account_path="m/44'/60'/0'/0/1")
    sa_account = Account.from_mnemonic(args.mnemonic, account_path="m/44'/60'/0'/0/2")

    token_abi, token_bytecode = load_artifact(artifacts_dir, "MockUSDC")
    service_abi, service_bytecode = load_artifact(artifacts_dir, "ServiceEscrow")

    token = deploy_contract(w3, token_abi, token_bytecode, [], deployer, gas_price)
    service = deploy_contract(
        w3, service_abi, service_bytecode, [token.address], deployer, gas_price
    )

    session_locked_topic = w3.keccak(
        text="SessionLocked(uint256,address,address,uint256,bytes32,bytes32,uint256)"
    )

    total_needed = args.amount * (args.samples * 3)
    mint_tx = token.functions.mint(ua, total_needed).transact(
        {"from": deployer, "gasPrice": gas_price}
    )
    w3.eth.wait_for_transaction_receipt(mint_tx)
    approve_tx = token.functions.approve(service.address, total_needed).transact(
        {"from": ua, "gasPrice": gas_price}
    )
    w3.eth.wait_for_transaction_receipt(approve_tx)
    permit_deadline = int(time.time()) + 365 * 24 * 3600

    rows = []
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
                    "description": "Agent-OSI latency experiment",
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

    server = x402_http.X402Server(
        accepts_builder=build_accepts,
        payment_executor=execute_payment,
        chain_id=w3.eth.chain_id,
        token_name="MockUSDC",
        token_version="1",
    )
    server.start()
    base_url = server.url
    try:
        for workload in workloads:
            random.seed(42)
            for sample in range(args.samples):
                t0 = time.perf_counter()
                if args.transport == "mock":
                    simulate_message(args.message_delay_ms)
                else:
                    xmtp_roundtrip(
                        xmtp_send_cmd, xmtp_recv_cmd, f"request:{workload}:{sample}"
                    )

                seed_value = args.image_seed
                if workload == "genai" and genai_mode == "real":
                    seed_value = args.image_seed + sample

                request_payload = {
                    "task_id": f"task-{workload}-{sample}",
                    "model_id": "model-v1",
                    "prompt": "cyberpunk cat",
                    "negative_prompt": "low quality",
                    "width": args.image_width,
                    "height": args.image_height,
                    "steps": args.image_steps,
                    "seed": seed_value,
                    "guidance_scale": 750,
                    "sampler": 1,
                    "deadline": 1700000000,
                    "metadata_uri": "ipfs://placeholder",
                }
                challenge = x402_http.request_payment_challenge(
                    base_url, request_payload
                )
                accept = challenge["accepts"][0]
                valid_after = 0
                valid_before = permit_deadline
                auth_nonce = w3.keccak(text=f"auth-{workload}-{sample}")
                v, r, s = sign_authorization(
                    ua_account,
                    w3.eth.chain_id,
                    token.address,
                    ua,
                    accept["payTo"],
                    int(accept["maxAmountRequired"]),
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
                t1 = time.perf_counter()
                payment_result = x402_http.send_payment(
                    base_url, request_payload, x_payment_header
                )
                if payment_result["status"] != 200:
                    raise SystemExit(
                        f"X-PAYMENT 失败: {payment_result.get('body')}"
                    )
                payment_response = payment_result.get("payment_response") or {}
                tx_hash = payment_response.get("transaction")
                if not tx_hash:
                    raise SystemExit("缺少 X-PAYMENT-RESPONSE transaction")
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
                session_id = require_event_id(
                    receipt, service.address, session_locked_topic, "Latency Session"
                )
                t2 = time.perf_counter()
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
                t3 = time.perf_counter()

                exec_started_at = time.time()
                if workload == "light":
                    validate_request_payload(request_payload)
                    verify_request_hash(request_payload, request_hash)
                    payload = build_light_payload()
                elif workload == "pipeline":
                    run_pipeline(
                        request_payload, request_hash, args.pipeline_steps, sa_account
                    )
                    payload = b"p" * args.pipeline_bytes
                else:
                    if genai_mode == "real":
                        assert comfyui_workflow is not None
                        prefix = f"{experiment_id}_sample{sample:03d}"
                        workflow = build_comfyui_workflow(
                            comfyui_workflow,
                            seed=seed_value,
                            width=args.image_width,
                            height=args.image_height,
                            steps=args.image_steps,
                            filename_prefix=prefix,
                        )
                        history = run_comfyui(
                            comfyui_url, workflow, args.comfyui_timeout
                        )
                        saved = save_comfyui_outputs(
                            comfyui_url, history, genai_output_dir, prefix
                        )
                        if saved == 0:
                            copied = copy_local_comfyui_outputs(
                                root, genai_output_dir, prefix
                            )
                            if copied == 0:
                                print("警告：未从 ComfyUI 获取到输出图像")
                    else:
                        time.sleep(
                            random.uniform(args.genai_delay_min, args.genai_delay_max)
                        )
                    payload = b"g" * args.genai_bytes
                exec_finished_at = time.time()
                t4 = time.perf_counter()

                if workload == "light":
                    cid = "inline"
                    t5 = t4
                else:
                    if args.ipfs_mode == "mock":
                        cid = mock_cid(payload)
                    elif args.ipfs_mode == "real":
                        cid = ipfs_add(args.ipfs_api, payload)
                    else:
                        try:
                            cid = ipfs_add(args.ipfs_api, payload)
                        except Exception as exc:
                            print(f"IPFS 不可用，改用 mock CID: {exc}")
                            cid = mock_cid(payload)
                    t5 = time.perf_counter()
                exec_log = {
                    "workload": workload,
                    "request_hash": request_hash.hex(),
                    "quote_id": quote_id.hex(),
                    "session_id": session_id,
                    "receipt_tx_hash": receipt["transactionHash"].hex(),
                    "cid": cid,
                    "payload_bytes": len(payload),
                    "exec_started_at": exec_started_at,
                    "exec_finished_at": exec_finished_at,
                }
                provenance = build_provenance(sa_account, exec_log)

                if workload == "light":
                    result_payload = json.dumps(
                        {
                            "cid": cid,
                            "result": json.loads(payload.decode("utf-8")),
                            "provenance": provenance,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                else:
                    result_payload = json.dumps(
                        {"cid": cid, "provenance": provenance},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                if args.transport == "mock":
                    simulate_message(args.message_delay_ms)
                else:
                    xmtp_roundtrip(xmtp_send_cmd, xmtp_recv_cmd, result_payload)
                t6 = time.perf_counter()

                messaging_ms = (t1 - t0 + t3 - t2 + t6 - t5) * 1000
                settlement_ms = (t2 - t1) * 1000
                execution_ms = (t5 - t3) * 1000
                ipfs_ms = (t5 - t4) * 1000

                rows.append(
                    {
                        "run_id": sample,
                        "workload": workload,
                        "messaging_ms": messaging_ms,
                        "settlement_ms": settlement_ms,
                        "execution_ms": execution_ms,
                        "ipfs_ms": ipfs_ms,
                        "provenance_cid": cid,
                        "provenance_exec_log_hash": provenance["exec_log_hash"],
                        "provenance_signature": provenance["signature"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

    finally:
        if _XMTP_BRIDGE is not None:
            _XMTP_BRIDGE.close()
        server.stop()

    summary = {}
    for row in rows:
        summary.setdefault(
            row["workload"],
            {"messaging": [], "settlement": [], "execution": [], "ipfs": []},
        )
        summary[row["workload"]]["messaging"].append(row["messaging_ms"])
        summary[row["workload"]]["settlement"].append(row["settlement_ms"])
        summary[row["workload"]]["execution"].append(row["execution_ms"])
        summary[row["workload"]]["ipfs"].append(row["ipfs_ms"])

    summary_rows = []
    for workload, values in summary.items():
        summary_rows.append(
            {
                "workload": workload,
                "n": len(values["messaging"]),
                "messaging_median": percentile(values["messaging"], 0.5),
                "messaging_p10": percentile(values["messaging"], 0.1),
                "messaging_p90": percentile(values["messaging"], 0.9),
                "settlement_median": percentile(values["settlement"], 0.5),
                "settlement_p10": percentile(values["settlement"], 0.1),
                "settlement_p90": percentile(values["settlement"], 0.9),
                "execution_median": percentile(values["execution"], 0.5),
                "execution_p10": percentile(values["execution"], 0.1),
                "execution_p90": percentile(values["execution"], 0.9),
                "ipfs_median": percentile(values["ipfs"], 0.5),
                "ipfs_p10": percentile(values["ipfs"], 0.1),
                "ipfs_p90": percentile(values["ipfs"], 0.9),
            }
        )

    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "workload",
                "n",
                "messaging_median",
                "messaging_p10",
                "messaging_p90",
                "settlement_median",
                "settlement_p10",
                "settlement_p90",
                "execution_median",
                "execution_p10",
                "execution_p90",
                "ipfs_median",
                "ipfs_p10",
                "ipfs_p90",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "workload",
                "messaging_ms",
                "settlement_ms",
                "execution_ms",
                "ipfs_ms",
                "provenance_cid",
                "provenance_exec_log_hash",
                "provenance_signature",
                "timestamp",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    config_snapshot = fig3_config.build_latency_config_snapshot(
        args, config, workloads_override=workloads_override
    )
    dependencies = {
        "anvil": {
            "rpc_url": args.rpc_url,
            "connected": w3.is_connected(),
            "chain_id": w3.eth.chain_id,
        },
        "xmtp": {
            "transport": args.transport,
            "enabled": args.transport == "xmtp",
            "send_cmd": xmtp_send_cmd,
            "recv_cmd": xmtp_recv_cmd,
        },
        "ipfs": {
            "mode": args.ipfs_mode,
            "api": args.ipfs_api,
            "enabled": args.ipfs_mode == "real",
        },
        "comfyui": {
            "configured": bool(comfyui_url and comfyui_workflow_path),
            "used": use_comfyui,
            "mode": genai_mode,
            "url": comfyui_url,
            "workflow": comfyui_workflow_path,
        },
    }
    extra = {
        "rpc_url": args.rpc_url,
        "samples": args.samples,
        "workloads": workloads,
        "transport": args.transport,
        "ipfs_mode": args.ipfs_mode,
        "message_delay_ms": args.message_delay_ms,
        "pipeline_steps": args.pipeline_steps,
        "pipeline_bytes": args.pipeline_bytes,
        "genai_bytes": args.genai_bytes,
        "genai_delay_min": args.genai_delay_min,
        "genai_delay_max": args.genai_delay_max,
        "genai_mode": genai_mode,
        "genai_output_dir": str(genai_output_dir),
        "image_width": args.image_width,
        "image_height": args.image_height,
        "image_steps": args.image_steps,
        "image_seed": args.image_seed,
        "comfyui_url": comfyui_url,
        "comfyui_workflow": comfyui_workflow_path,
        "comfyui_used": use_comfyui,
        "xmtp_send_cmd": xmtp_send_cmd,
        "xmtp_recv_cmd": xmtp_recv_cmd,
        "provenance_enabled": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    env_payload = fig3_env.build_env(
        experiment_id=experiment_id,
        script="collect_latency.py",
        config=config_snapshot,
        dependencies=dependencies,
        extra=extra,
        root_dir=root,
    )
    fig3_env.write_env(out_path.parent / "fig3b_env.json", env_payload)
    fig3_env.write_env(out_path.parent / "latency_env.json", env_payload)


if __name__ == "__main__":
    main()
