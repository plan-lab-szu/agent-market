# 本地基础设施

## 组件
- Anvil：本地EVM链
- IPFS Kubo：内容寻址存储
- XMTP Node：A2A消息传输

## 启动
```bash
./scripts/infrastructure/start_infrastructure.sh
```

如未安装 XMTP 节点，可设置 `SKIP_XMTP=1` 跳过：

```bash
SKIP_XMTP=1 ./scripts/infrastructure/start_infrastructure.sh
```

## 健康检查
```bash
python scripts/infrastructure/health_check.py
```

## 停止
```bash
./scripts/infrastructure/stop_infrastructure.sh
```

## XMTP 节点说明
默认使用 `xmtp-node` 命令启动节点。如需使用其他二进制或参数，请设置环境变量：

```bash
export XMTP_CMD="/path/to/xmtp-node --port 5556"
```

### 本地编译 xmtp-node-go
建议在本地编译 `xmtp-node-go`，并将产物路径填入 `XMTP_CMD`：

```bash
git clone https://github.com/xmtp/xmtp-node-go.git
cd xmtp-node-go
make build
export XMTP_CMD="$(pwd)/build/xmtp-node --port 5556"
```

节点通常需要数据库支持（如 Postgres）。请根据 xmtp-node-go 文档配置数据库连接。

### XMTP CLI 命令配置
延迟实验默认使用真实 XMTP 传输。请在 `scripts/infrastructure/env.json` 配置：

- `xmtp_send_cmd`: 发送命令（例如：`node scripts/xmtp_cli/xmtp_send.js`）
- `xmtp_recv_cmd`: 接收命令（例如：`node scripts/xmtp_cli/xmtp_recv.js`）

若未显式设置 `XMTP_ENV`，CLI 会读取 `env.json` 中的 `xmtp_host/xmtp_port` 并使用本地节点。

运行时需提供 XMTP_PEER 与密钥相关环境变量（见 `scripts/xmtp_cli/*.js`）。

## ComfyUI（GenAI）
若要在 Fig.3(b) 使用远端 ComfyUI：

- 在 `scripts/infrastructure/env.json` 中填入 `comfyui_url` 与 `comfyui_workflow`
- 运行脚本时设置环境变量：

```bash
export COMFYUI_URL="http://<4090-host>:8188"
export COMFYUI_WORKFLOW="/path/to/workflow.json"
```

GenAI 负载必须使用真实生成（不允许模拟），请确保 ComfyUI 可访问。
