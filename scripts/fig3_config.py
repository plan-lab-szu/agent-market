#!/usr/bin/env python3
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "rpc_url": "http://127.0.0.1:8545",
    "gas_price_gwei": 20,
    "amount": 1_000_000,
    "xmtp": {
        "send_cmd": "",
        "recv_cmd": "",
    },
    "ipfs": {
        "api": "http://127.0.0.1:5001",
    },
    "costs": {
        "samples": 100,
    },
    "latency": {
        "samples": 100,
        "workloads": ["light", "pipeline", "genai"],
        "transport": "xmtp",
        "ipfs_mode": "real",
        "message_delay_ms": 40,
        "pipeline_steps": 5,
        "pipeline_bytes": 256 * 1024,
        "genai_bytes": 1024 * 1024,
        "genai_delay_min": 5.0,
        "genai_delay_max": 15.0,
        "genai_mode": "auto",
        "genai_output_dir": "outputs/fig3_genai_images",
        "image_width": 1024,
        "image_height": 1024,
        "image_steps": 30,
        "image_seed": 42,
        "comfyui_url": "",
        "comfyui_workflow": "",
        "comfyui_timeout": 180,
    },
    "tps": {
        "duration": 60,
        "warmup": 5,
        "trials": 5,
        "concurrency": [10, 50, 100, 200, 500],
        "transport": "xmtp",
        "message_delay_ms": 40,
        "execution_delay_ms": 20,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise SystemExit(f"配置文件不存在: {path}")
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit("配置文件必须为 JSON 对象")
    return _deep_merge(DEFAULT_CONFIG, payload)


def _apply_common(args, config: Dict[str, Any]) -> None:
    if "rpc_url" in config:
        args.rpc_url = config["rpc_url"]
    if "gas_price_gwei" in config and hasattr(args, "gas_price_gwei"):
        args.gas_price_gwei = config["gas_price_gwei"]
    if "amount" in config and hasattr(args, "amount"):
        args.amount = config["amount"]
    xmtp = config.get("xmtp", {})
    if hasattr(args, "xmtp_send_cmd") and xmtp.get("send_cmd"):
        args.xmtp_send_cmd = xmtp["send_cmd"]
    if hasattr(args, "xmtp_recv_cmd") and xmtp.get("recv_cmd"):
        args.xmtp_recv_cmd = xmtp["recv_cmd"]
    ipfs = config.get("ipfs", {})
    if hasattr(args, "ipfs_api") and ipfs.get("api"):
        args.ipfs_api = ipfs["api"]


def apply_costs_config(args, config: Dict[str, Any]) -> None:
    _apply_common(args, config)
    costs = config.get("costs", {})
    if "samples" in costs:
        args.samples = int(costs["samples"])


def apply_latency_config(args, config: Dict[str, Any]) -> Optional[List[str]]:
    _apply_common(args, config)
    latency = config.get("latency", {})
    if "samples" in latency:
        args.samples = int(latency["samples"])
    if "transport" in latency:
        args.transport = latency["transport"]
    if "ipfs_mode" in latency and getattr(args, "ipfs_mode", None) is None:
        args.ipfs_mode = latency["ipfs_mode"]
    if "message_delay_ms" in latency:
        args.message_delay_ms = int(latency["message_delay_ms"])
    if "pipeline_steps" in latency:
        args.pipeline_steps = int(latency["pipeline_steps"])
    if "pipeline_bytes" in latency:
        args.pipeline_bytes = int(latency["pipeline_bytes"])
    if "genai_bytes" in latency:
        args.genai_bytes = int(latency["genai_bytes"])
    if "genai_delay_min" in latency:
        args.genai_delay_min = float(latency["genai_delay_min"])
    if "genai_delay_max" in latency:
        args.genai_delay_max = float(latency["genai_delay_max"])
    if "genai_mode" in latency:
        args.genai_mode = latency["genai_mode"]
    if "genai_output_dir" in latency:
        args.genai_output_dir = latency["genai_output_dir"]
    if "image_width" in latency:
        args.image_width = int(latency["image_width"])
    if "image_height" in latency:
        args.image_height = int(latency["image_height"])
    if "image_steps" in latency:
        args.image_steps = int(latency["image_steps"])
    if "image_seed" in latency:
        args.image_seed = int(latency["image_seed"])
    if "comfyui_url" in latency and latency["comfyui_url"]:
        args.comfyui_url = latency["comfyui_url"]
    if "comfyui_workflow" in latency and latency["comfyui_workflow"]:
        args.comfyui_workflow = latency["comfyui_workflow"]
    if "comfyui_timeout" in latency:
        args.comfyui_timeout = int(latency["comfyui_timeout"])
    workloads = latency.get("workloads")
    if isinstance(workloads, list) and workloads:
        return [str(item) for item in workloads]
    return None


def apply_tps_config(args, config: Dict[str, Any]) -> None:
    _apply_common(args, config)
    tps = config.get("tps", {})
    if "duration" in tps:
        args.duration = int(tps["duration"])
    if "warmup" in tps:
        args.warmup = int(tps["warmup"])
    if "trials" in tps:
        args.trials = int(tps["trials"])
    if "transport" in tps:
        args.transport = tps["transport"]
    if "message_delay_ms" in tps:
        args.message_delay_ms = int(tps["message_delay_ms"])
    if "execution_delay_ms" in tps:
        args.execution_delay_ms = int(tps["execution_delay_ms"])
    if "concurrency" in tps:
        values = tps["concurrency"]
        if isinstance(values, list):
            args.concurrency = ",".join(str(item) for item in values)
        else:
            args.concurrency = str(values)


def _build_base_config(base: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if base is None:
        return deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, base)


def build_costs_config_snapshot(args, base: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = _build_base_config(base)
    config["rpc_url"] = args.rpc_url
    config["gas_price_gwei"] = args.gas_price_gwei
    config["amount"] = args.amount
    config["costs"]["samples"] = args.samples
    return config


def build_latency_config_snapshot(
    args, base: Optional[Dict[str, Any]], workloads_override: Optional[List[str]] = None
) -> Dict[str, Any]:
    config = _build_base_config(base)
    config["rpc_url"] = args.rpc_url
    config["gas_price_gwei"] = args.gas_price_gwei
    config["amount"] = args.amount
    config["xmtp"]["send_cmd"] = getattr(args, "xmtp_send_cmd", "")
    config["xmtp"]["recv_cmd"] = getattr(args, "xmtp_recv_cmd", "")
    config["ipfs"]["api"] = getattr(args, "ipfs_api", config["ipfs"].get("api"))
    latency = config["latency"]
    latency["samples"] = args.samples
    latency["transport"] = args.transport
    latency["ipfs_mode"] = args.ipfs_mode
    latency["message_delay_ms"] = args.message_delay_ms
    latency["pipeline_steps"] = args.pipeline_steps
    latency["pipeline_bytes"] = args.pipeline_bytes
    latency["genai_bytes"] = args.genai_bytes
    latency["genai_delay_min"] = args.genai_delay_min
    latency["genai_delay_max"] = args.genai_delay_max
    latency["genai_mode"] = getattr(args, "genai_mode", latency.get("genai_mode", "auto"))
    latency["genai_output_dir"] = getattr(
        args, "genai_output_dir", latency.get("genai_output_dir", "")
    )
    latency["image_width"] = args.image_width
    latency["image_height"] = args.image_height
    latency["image_steps"] = args.image_steps
    latency["image_seed"] = args.image_seed
    latency["comfyui_url"] = getattr(args, "comfyui_url", "")
    latency["comfyui_workflow"] = getattr(args, "comfyui_workflow", "")
    latency["comfyui_timeout"] = getattr(args, "comfyui_timeout", latency.get("comfyui_timeout", 180))
    if workloads_override:
        latency["workloads"] = list(workloads_override)
    elif getattr(args, "workload", "all") == "all":
        latency["workloads"] = ["light", "pipeline", "genai"]
    else:
        latency["workloads"] = [args.workload]
    return config


def build_tps_config_snapshot(args, base: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = _build_base_config(base)
    config["rpc_url"] = args.rpc_url
    config["gas_price_gwei"] = args.gas_price_gwei
    config["amount"] = args.amount
    config["xmtp"]["send_cmd"] = getattr(args, "xmtp_send_cmd", "")
    config["xmtp"]["recv_cmd"] = getattr(args, "xmtp_recv_cmd", "")
    tps = config["tps"]
    tps["duration"] = args.duration
    tps["warmup"] = args.warmup
    tps["trials"] = args.trials
    tps["transport"] = args.transport
    tps["message_delay_ms"] = args.message_delay_ms
    tps["execution_delay_ms"] = args.execution_delay_ms
    tps["concurrency"] = [
        int(item) for item in str(args.concurrency).split(",") if str(item).strip()
    ]
    return config
