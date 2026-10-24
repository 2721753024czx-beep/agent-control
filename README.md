# Networked Agent Remote Control

通过飞书手机端远程控制无公网 IP 电脑上的本地 OpenCode Agent，查看工作目录、处理一次性审批并接收最终答案。当前受限执行链路针对 OpenCode；其他 Agent 引擎尚未完成同等级安全适配。

设计文档见 `docs/design.md`，飞书应用创建与 Tailscale 组网见 `docs/phase2-setup.md`。

Windows 单机运行 OpenCode + 飞书请直接使用 [Windows 部署指南](docs/windows-opencode-feishu.md) 和 `scripts/windows/install.ps1`。Hub 与 Node 同机时不需要 Tailscale。

支持发送 PDF 文件后再发送文字处理要求；文字提取、扫描页 OCR 安装与限制见 [PDF 附件指南](docs/pdf-attachments.md)。

## 目录结构

```
├── docs/
│   ├── design.md            # 架构设计 + 数据库结构
│   └── phase2-setup.md      # 飞书建应用 + Tailscale 组网 + lark-cli 调试
├── scripts/
│   └── feishu_cli_setup.py  # lark-cli 一键绑定/发测试消息
├── hub/                     # 控制中心（跑在其中一台电脑上）
│   ├── hub.py               # FastAPI 服务 + 飞书 WS 长连接
│   ├── feishu_client.py     # 飞书 SDK 封装（每 Agent 一个应用）
│   ├── db.py                # SQLite 初始化与读写
│   ├── manage.py            # 运行时管理：改显示名/加 Agent/查任务
│   ├── config.json          # 部署配置（复制自 config.example.json）
│   └── config.example.json
├── node/                    # 节点进程（每台电脑跑一个）
│   ├── node.py              # 轮询任务 + 上报结果
│   ├── engines.py           # 受限 serve；CLI 保留本地兼容但手机任务拒绝
│   ├── config.json          # 部署配置（复制自 config.example.json）
│   └── config.example.json
└── e2e_test.py              # 本地端到端流程测试（无需飞书）
```

## 部署步骤

### 1. 前置准备
- 按 `docs/phase2-setup.md` 完成：创建飞书应用、开启机器人、加权限、开**长连接模式**事件订阅、发布版本。
- 所有设备装好 Tailscale 并登录同一账号，记下 Hub 电脑的 Tailscale IP。
- 在每台 Node 电脑启动受密码保护的回环 OpenCode `serve`；安全规则和 macOS 启动方式见 [手机端控制设计](docs/mobile-control-design.md)。同机 Windows 安装可直接使用自动配置的安装器。

### 2. 配置 Hub（在 Hub 电脑上）

```bash
cd hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# 编辑 config.json：填入飞书 App ID/Secret、设备 token、显示名和本人 open_id
python hub.py          # 监听 0.0.0.0:8000
```

`config.json` 关键字段：

| 字段 | 说明 |
|------|------|
| `app_id` / `app_secret` | 飞书应用凭证（每个 Agent 一个应用） |
| `device_id` | 该 Agent 归属的设备，需与 node 的 `device_id` 一致 |
| `display_name` | **可编辑显示名**，回复时拼成 `【display_name】...` |
| `engine_type` | 手机任务必须为 `serve`；`cli` 会被 Node 拒绝 |
| `exec_path` | Node 本机受保护服务地址，如 `http://127.0.0.1:4096` |
| `work_dir` | Agent 的默认工作目录；须在 Node 的 `allowed_work_dirs` 内 |
| `allowed_open_ids` | 可使用 `/edit` 和 `/trash` 的飞书发送者白名单；示例占位值必须替换 |

启动后日志出现 `ws started for ...` 即表示与飞书的长连接已建立。

### 3. 配置 Node（每台目标电脑上）

```bash
cd node
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# 编辑 config.json：填 Hub 地址、匹配的 device_id/token、serve_password 和允许目录
python node.py
```

### 4. 使用
1. 手机飞书搜索机器人名（如 `PC1-OpenCode`），发起单聊，发送文本指令。审批卡片可点「批准一次」或「拒绝」；需在飞书后台启用长连接 `card.action.trigger` 回调并发布应用版本，文字短码命令仍可备用。
2. 机器人先回 `【PC1-OpenCode】收到，正在处理…`，完成后回最终答案。
3. **修改显示名**（两种方式，均即时生效）：
   ```bash
   curl -X POST http://<hub>:8000/agents/<app_id>/display_name \
     -H 'Content-Type: application/json' \
     -d '{"admin_token":"<admin_token>","display_name":"新名字"}'
   # 或直接在 Hub 机器上
   python3 hub/manage.py rename <app_id> 新名字
   ```
   下次回复即显示 `【新名字】...`。

### 5. 运维工具

```bash
python3 hub/manage.py agents                  # 列出所有 Agent
python3 hub/manage.py add-agent --json '{...}'  # 加 Agent（后台建好应用后）
python3 hub/manage.py tasks --status pending  # 查积压任务
python3 scripts/feishu_cli_setup.py bind      # 用 lark-cli 一键绑定所有 Agent 配置
python3 scripts/feishu_cli_setup.py send --profile opencode --chat-id oc_xxx --text "测试"
```

### 6. 本地端到端测试（不依赖飞书）

```bash
python3 e2e_test.py
```
会在临时配置/数据库中模拟「消息进入 → 建任务 → Node 拉取 → 受限 serve → 上报 → 回复」，不使用真实飞书凭据。

## 说明与注意事项

- **长连接收事件**：飞书事件经 WebSocket 主动推送到 Hub，无需公网回调 URL；Node 通过 Tailscale 连 Hub。
- **上下文**：Node 每次任务前读取当前工作目录的 `.agent-memory` 快照。`serve` 每次新建 OpenCode session；目前不承诺跨消息的短期对话会话记忆。
- **受限执行**：`node/engines.py` 校验 OpenCode 服务器权限，禁用危险和未知工具，轮询只读权限请求，并只允许一次性批准。`/edit` 走 Node 的单文件草案与差异审批，不开放 OpenCode 原生编辑工具。
- **回复渲染**：最终答案以**飞书消息卡片**（`msg_type: interactive` + `lark_md`）发送，原生渲染 markdown（粗体/标题/列表/行内代码/代码块），不再显示原始 `**`/`` ` `` 字符；`【显示名】` 作为卡片标题。超长答案按段落拆成多条卡片。实现见 `hub/feishu_client.py::reply_card` 与 `hub/hub.py::reply_final`。
- **lark-oapi SDK 兼容坑（已实测修复）**：
  - 事件注册方法名是 `register_p2_im_message_receive_v1`（**单处理器**，靠事件的 `chat_type` 区分单聊/群聊），不是 `register_p2p_im_message_receive_v1`，版本不同可能报 `AttributeError`。
  - 请求对象必须用 **builder** 构造：`ReplyMessageRequest.builder().message_id(...).request_body(body).build()`。直接 `ReplyMessageRequest()` 再属性赋值会缺 `uri`，导致 `_build_url` 里 `'NoneType' has no attribute 'startswith'`。
  - 响应体模型用 `d` 字典初始化：`ReplyMessageRequestBody()` 后属性赋值（`content` 为 JSON 字符串 `{"text": "..."}`，`msg_type` 为 `"text"`）。
- **安全**：Node 拉任务/上报均校验设备 token；飞书侧通过"使用范围"限制仅你可与机器人对话。请勿将 token 泄露给他人。
