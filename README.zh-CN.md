# AgentMarket 🚀

[English](README.md) | [中文](README.zh-CN.md)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Solidity](https://img.shields.io/badge/Solidity-0.8.20-363636)
![JavaScript](https://img.shields.io/badge/JavaScript-ES2020-F7DF1E?logo=javascript&logoColor=000)
![Shell](https://img.shields.io/badge/Shell-Bash-4EAA25?logo=gnu-bash&logoColor=fff)
![License](https://img.shields.io/badge/License-MIT-green)
![arXiv](https://img.shields.io/badge/arXiv-TBA-lightgrey)

AgentMarket 是 Agent-OSI 的概念验证实现，用于按请求付费的智能体服务。系统实现了完整流程（发现 → 报价 → 支付 → 执行 → 交付），并生成实验评估所需的 Fig.3 图表。

## 目录

- [亮点](#亮点)
- [架构](#架构)
- [可视化素材](#可视化素材)
- [仓库结构](#仓库结构)
- [工作负载](#工作负载)
- [指标说明](#指标说明)
- [环境依赖](#环境依赖)
- [快速开始](#快速开始)
- [运行实验](#运行实验)
- [常见问题](#常见问题)
- [许可证](#许可证)

## 亮点 ✨

- 端到端按请求付费流程，回执绑定结算
- 规范化执行日志 + ECDSA 可信溯源
- 真实 A2A 消息（XMTP）、内容交付（IPFS）与 GenAI 执行（ComfyUI SDXL）
- 可复现实验脚本（Fig.3 成本/延迟/吞吐）

## 架构 🧱

- **L6 语义互操作与编排**：能力清单、绑定/校验、会话状态机
- **L5 可验证执行与溯源**：规范化执行日志 → SHA-256 → ECDSA 签名；输出绑定 CID
- **L4 结算与计量**：402 风格支付挑战；escrow lock/release；链上事件解码校验回执
- **L3 身份/授权**：身份密钥对；签名 quote 与溯源；nonce + request hash 绑定
- **L2 A2A 消息与路由**：异步消息线程；签名消息封装
- **L1 连接与安全传输**：HTTPS/TLS 支撑消息/RPC/IPFS

## 可视化素材 🖼️

原型技术栈表：

![Prototype stack table](visual_assets/table.png)

实验图：

![Fig3a cost](visual_assets/fig3a.png)
![Fig3b latency](visual_assets/fig3b.png)
![Fig3c throughput](visual_assets/fig3c.png)

## 仓库结构 🗂️

- `evm/`：Solidity 合约与 Foundry 测试
- `scripts/`：实验脚本、绘图与基础设施辅助
- `configs/`：实验配置
- `visual_assets/`：文档图表与表格

实验输出会写入 `outputs/`（已忽略）。

## 工作负载 🔬

三类 workload 流程一致，但执行/交付不同：

- **Light (no-gen)**：校验 + 溯源；1–5KB JSON 内联返回
- **Pipeline (K-step)**：固定 K 步流水线 + 256KB 产物，通过 IPFS CID 交付
- **GenAI (image/LLM)**：SDXL 图像生成，通过 IPFS CID 交付

Pipeline 默认配置：

- `K = 5`（`configs/fig3.json`）
- 每步对前一状态做哈希并签名（`scripts/collect_latency.py` 的 `run_pipeline`）

## 指标说明 📈

- **Fig.3a 成本**：单次会话 gasUsed（不含手续费）
- **Fig.3b 延迟**：消息、结算确认、执行（执行包含 IPFS 上传）
- **Fig.3c 吞吐**：msg/s、tx/s、sessions/s（UA → 1 SA）

## 环境依赖 ✅

- Python 3.10+
- Node.js（XMTP bridge）
- Foundry（合约）
- Anvil（本地链）
- IPFS Kubo
- ComfyUI（SDXL 工作流）
- XMTP 节点

## 快速开始 ⚡

可选：启动本地基础设施

```bash
./scripts/infrastructure/start_infrastructure.sh
```

## 运行实验 🧪

### Fig3a（成本）

```bash
bash scripts/run_cost_exp.sh
```

### Fig3b（延迟：XMTP + IPFS + ComfyUI）

```bash
XMTP_PEER="0x70997970C51812dc3A010C7d01b50e0d17dc79C8" \
COMFYUI_URL="http://127.0.0.1:8188" \
COMFYUI_WORKFLOW="scripts/infrastructure/comfyui_sdxl_1024_30.json" \
bash scripts/run_latency_exp.sh
```

### Fig3c（吞吐：XMTP）

```bash
XMTP_PEER="0x70997970C51812dc3A010C7d01b50e0d17dc79C8" \
bash scripts/run_throughput_exp.sh
```

## 常见问题 🛠️

- 建议使用 2s 出块时间保证结算稳定。
- Fig3c 并发 500 需要 Anvil 账户 >= 502。
- IPFS 不可用会导致 Fig3b 上传失败，请检查 `IPFS_API`。
- XMTP 通过 bridge 运行，出现 DB/安装错误可更换 `XMTP_PRIVATE_KEY`。

## 许可证 📜

本项目基于 [MIT License](LICENSE) 开源。
