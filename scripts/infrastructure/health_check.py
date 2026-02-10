#!/usr/bin/env python3
import json
import socket
from pathlib import Path
from urllib import request


def load_env():
    root = Path(__file__).resolve().parents[2]
    env_path = root / "scripts" / "infrastructure" / "env.json"
    with env_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def check_anvil(rpc_url: str):
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": "web3_clientVersion", "id": 1}
    ).encode("utf-8")
    req = request.Request(
        rpc_url, data=payload, headers={"Content-Type": "application/json"}
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def check_ipfs(api_url: str):
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        req = request.Request(
            f"{api_url}/api/v0/version", data=b"", method="POST"
        )
        with opener.open(req, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def check_port(host: str, port: int):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False


def check_comfyui(base_url: str):
    if not base_url:
        return None
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(f"{base_url}/system_stats", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def main():
    env = load_env()
    results = {
        "anvil": check_anvil(env["anvil_rpc"]),
        "ipfs": check_ipfs(env["ipfs_api"]),
        "xmtp": check_port(env["xmtp_host"], int(env["xmtp_port"])),
    }

    comfyui_url = env.get("comfyui_url", "")
    comfyui_status = check_comfyui(comfyui_url)
    if comfyui_status is not None:
        results["comfyui"] = comfyui_status

    for name, ok in results.items():
        status = "ok" if ok else "failed"
        print(f"{name}: {status}")


if __name__ == "__main__":
    main()
