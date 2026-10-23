# Phase 2 — 环境与组网配置指南

> 目标：创建 2 个飞书应用（PC1-OpenCode、PC2-Claude），并打通 Hub↔Node 的 Tailscale 虚拟内网。
> 关键结论：**收事件用飞书 WebSocket 长连接，不需要配置公网回调 URL。**

## 2.1 创建飞书应用（每个 Agent 一个）

每个 Agent 重复以下步骤，创建时用对应名字。示例创建 2 个：

| 应用名 | 用途 | 目标设备 |
|--------|------|---------|
| PC1-OpenCode | 控制 PC1 的 OpenCode | PC1 |
| PC2-Claude | 控制 PC2 的 Claude | PC2 |

1. 打开飞书开放平台：https://open.feishu.cn/app
2. 右上角 **创建企业自建应用**，填写应用名称（如 `PC1-OpenCode`）、应用描述、图标，创建。
3. 进入应用 → **凭证与基础信息**，记下两个值（Phase 3 写进配置）：
   - **App ID**（形如 `cli_a8f7x2...`）
   - **App Secret**（形如 `xxxxxxxx`）
4. **添加应用能力 → 机器人**，点击 **启用机器人**。
5. 左侧 **权限管理**，开通以下权限并等待审核通过（企业自建应用通常即时通过）：
   - `im:message` —— 读取用户发给机器人的单聊消息
   - `im:message:send_as_bot` —— 以机器人身份发送消息
   - `im:chat` —— 获取群组信息（回复单聊消息可选，建议开）
6. 左侧 **事件与回调 → 事件订阅**：
   - 事件订阅方式选择 **长连接模式**（关键！不需要填请求地址）
   - 添加事件 → 搜索并添加 **接收消息 `im.message.receive_v1`**
   - 保存。
   - 若要使用审批卡片按钮，还需在 **事件与回调 → 回调配置** 中选择长连接并订阅 `card.action.trigger`；按钮无法使用时仍可发送 `/approve <短码>` 或 `/reject <短码>`。
7. 左侧 **版本管理与发布**：创建版本 → 填写版本说明 → 发布。
   - 若你是企业管理员，直接通过；否则等管理员审核。
   - **使用范围**（发布时设置）设为"仅我 / 指定成员"，保证只有你能跟机器人对话（安全要求）。
8. 重复以上步骤创建第 2 个应用（PC2-Claude）。

> 补充：飞书机器人**没有公开改名 API**。机器人在通讯录里的名字 = 应用名（后台改一次）。"可编辑显示名"由 Hub 在回复时拼 `【DisplayName】:` 前缀实现（见 Phase 3）。

## 2.2 Tailscale 组网（Hub ↔ Node 跨网络互通）

飞书事件走长连接出站，Hub **不需要**公网入口；但 **Node 要主动连 Hub**，跨网络时必须 Tailscale。

1. 每台电脑装 Tailscale：
   - macOS：`brew install --cask tailscale`
   - Windows：从 https://tailscale.com/download 安装
   - Linux：`curl -fsSL https://tailscale.com/install.sh | sh`
2. 所有设备登录**同一个** Tailscale 账号：
   ```bash
   tailscale up
   # 浏览器弹出登录，选同一账号
   ```
3. 验证互通：
   ```bash
   tailscale status          # 看到所有设备在列
   tailscale ip -4           # 拿到本机 Tailscale IP（100.x.y.z）
   ```
4. 记下**运行 Hub 的那台电脑**的 Tailscale IP（如 `100.64.1.2`），Phase 3 写进 Node 配置：
   ```json
   { "hub_url": "http://100.64.1.2:8000" }
   ```
5. （可选）长连接稳定性更好，可把 `tailscaled` 设为开机自启。

## 2.3 配置清单（Phase 3 需要用到的值）

| 项 | 值 | 来源 |
|----|----|------|
| PC1-OpenCode 的 App ID | `cli_xxx1` | 飞书后台 |
| PC1-OpenCode 的 App Secret | `secret1` | 飞书后台 |
| PC2-Claude 的 App ID | `cli_xxx2` | 飞书后台 |
| PC2-Claude 的 App Secret | `secret2` | 飞书后台 |
| Hub 的 Tailscale IP | `100.64.1.2` | `tailscale ip -4` |
| Hub 监听端口 | `8000` | 自定义 |

## 2.4 验证方法

- **长连接是否生效**：飞书后台"事件订阅"页显示 **连接正常/已连接**，说明 WS 通道已建立。
- **Tailscale 是否通**：在 Node 机器上 `curl http://100.64.1.2:8000/health`（Phase 3 部署后）。

## 2.5 官方 lark-cli 调试工具（推荐安装）

飞书官方 CLI（`@larksuite/cli`，命令名 `lark-cli`），适合建好应用后做运维/调试。注意：它**不能**全自动创建"企业自建机器人应用"（`config init --new` 需浏览器确认；`apps +create` 建的是 Miaoda 低代码应用），但能自动完成**绑定、登录、发消息、看事件**等运行时操作。

### 安装

```bash
npx @larksuite/cli@latest install
lark-cli --version
```

### 一键绑定所有 Agent（推荐）

```bash
python3 scripts/feishu_cli_setup.py bind
```

脚本读取 `hub/config.json`，为每个 Agent 用其 `agent_name` 创建**命名配置**（profile），并自动跳过仍是占位值的条目。之后对每个 profile 做一次浏览器登录：

```bash
lark-cli auth login --profile opencode --recommend
lark-cli auth login --profile claude --recommend
```

### 常用调试命令

```bash
lark-cli config show                          # 查看当前绑定/凭据状态
lark-cli whoami                               # 当前身份、应用、profile、token
lark-cli profile list                         # 列出所有命名配置
lark-cli auth status                          # 登录状态与已授权 scope

# 发测试消息（--profile 选择对应 Agent 的配置）
lark-cli im +messages-send --profile opencode --chat-id oc_xxx --text "测试"
# 或使用脚本封装
python3 scripts/feishu_cli_setup.py send --profile opencode --chat-id oc_xxx --text "测试"

# 实时事件（WebSocket 长连接消费，需应用已在后台开启长连接事件订阅）
lark-cli event list                           # 查看可消费的事件
lark-cli event consume im.message.receive_v1  # 前台消费，看事件原文
```

### 找不到 chat-id？

给机器人发一条消息后，用以下任一方式取 `oc_xxx`：

```bash
lark-cli im +chats-list                        # 列出会话
# 或到飞书后台"事件订阅"日志里看收到的 im.message.receive_v1 事件里的 chat_id
```

### 约束提醒

- 建应用、开机器人、加权限、事件订阅切长连接、发布版本 —— **仍须人工在后台做一次**（见 2.1），CLI 无法替代。
- `event consume` 是 CLI 自己的事件总线，只是方便调试看原始事件；实际收发仍由 hub（Python SDK）负责。
