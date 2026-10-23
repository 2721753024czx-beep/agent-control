# Windows OpenCode + 飞书部署

## 运行架构

Windows 电脑同时运行 Hub、Node 和 OpenCode：

```text
手机飞书 → 飞书云端 → WebSocket（出站）→ Windows Hub
                                      → 本机 Node → 受限 OpenCode serve → 飞书回复
```

不需要公网 IP、端口映射、Mac、SSH 或 Tailscale。飞书 CLI 只用于应用绑定和调试；运行时由 `lark-oapi` WebSocket 直接收消息。

OpenCode 官方在 Windows 上更推荐 WSL，但也明确支持直接运行。本仓库提供原生 Windows 安装器；如果原生 OpenCode 的工具兼容性不满足项目，再使用 WSL 方案。安装器不会把飞书文本交给 `cmd.exe`：`.exe` 直接调用，官方 npm shim 则解析为 `node.exe + CLI 脚本`。

## 前置条件

1. Windows 10/11，Python 3.9+，命令 `python --version` 可用。
2. 官方 OpenCode，命令 `opencode --version` 可用。Scoop/Chocolatey 的 `.exe` 和官方 npm 的 `.cmd` shim 均支持。
3. OpenCode 已完成模型提供商登录，并能在目标目录执行：

   ```powershell
   cd C:\你的项目
   opencode run "只回复 WINDOWS_OPENCODE_OK"
   ```

4. 飞书企业自建应用已启用机器人、发布版本、使用范围仅包含本人，并开通：
   - `im:message`
   - `im:message:send_as_bot`
   - 长连接事件 `im.message.receive_v1`
5. 已安装的 `lark-cli --version` 可正常执行。
6. 该飞书 App ID 必须只由 Windows Hub 使用。若 Mac Hub 正在使用同一 App ID，先停止 Mac Hub；或者为 Windows 创建独立飞书应用。两个 Hub 同时连接同一应用时，不能保证手机事件落到 Windows。

## 一键安装

推荐把 `dist/agent-control-windows.zip` 复制到 Windows 并解压；部署包采用文件白名单生成，不含当前机器的 `config.json`、App Secret、Device token、数据库或日志。也可以复制完整源码后执行同样步骤。

在普通 PowerShell 中进入解压后的 `agent-control` 根目录：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\install.ps1
```

安装器会依次：

1. 检查 Python、`opencode.exe`、工作目录和飞书凭据。
   安装器会要求输入 `WINDOWS`，确认该飞书应用未被其他 Hub 同时使用。
2. 在安装其他组件前只执行一次 OpenCode 冒烟任务，提前确认模型登录和额度可用。
3. 创建 `.venv`、安装固定版本依赖并运行单元测试与隔离 E2E。
4. 首次安装时备份旧配置/数据库，避免 Mac 的 Agent 映射混入 Windows。
5. 生成随机的 Admin/Device token。
6. 写入本机一体化 Hub/Node 配置，Node 固定访问 `127.0.0.1:8000`。
7. 初始化目标工作目录的 `.agent-memory` 与 `AGENTS.md` 约定。
   同时安装项目级 `.agents/skills/shared-memory`；这是 Windows 原生 Python 实现，不依赖 Bash，并使用文件锁保护并发追加。
8. 可选调用现有 lark-cli 绑定应用。
9. 注册四个当前用户登录自启任务：`AgentControl-OpenCode`、`AgentControl-Hub`、`AgentControl-Node`、`AgentControl-History`。
10. 启动服务并执行 Hub、受密码保护的 OpenCode 健康检查与一次历史快照。

配置文件含 App Secret，不要上传或发送给他人。

默认会消耗一次很小的 OpenCode 模型请求做安装前验收；只有已经自行验证模型时才使用 `-SkipOpenCodeSmokeTest` 跳过。旧配置与数据库备份位于 `backups/`。

## 手机端验收

先在 Windows PowerShell 启动带等待的真实链路验收：

```powershell
.\scripts\windows\verify.ps1 -WaitForFeishuSeconds 180
```

看到提示后，用手机发送 `/status`。脚本只有观察到本次新产生的 `/status` 控制事件，才会把“手机飞书 → Windows Hub → Node”标记为通过；手机同时应收到“正在处理”和状态回复。

在手机飞书中依次发送：

```text
/status
/skills
/memory
/workspace current
只回复 WINDOWS_FEISHU_OPENCODE_OK
/history
```

预期：

- `/status` 不调用模型，返回当日预算和冷却状态。
- `/skills`、`/memory` 读取 Windows 当前工作目录。
- 普通文本触发 OpenCode，先收到“正在处理”，然后收到最终卡片。
- `/history` 能看到 `task_start`、`task_done` 等记录。

## 固定执行与额度保护流程

每个普通任务严格执行：

1. 检查额度冷却、每日任务数和每日累计模型运行时。
2. 在调用模型前写入 `task_start`，只记录 prompt 摘要，不保存原文。
3. 读取当前工作目录 `.agent-memory` 并加入“只作上下文”的提示。
4. 向受限的 `opencode serve --pure` 提交任务。Node 对每次任务只显式禁用 shell、编辑、补丁、子任务和未知工具；只读工具由服务端 `read: ask` 等规则控制，不能在逐请求映射中设为 `true`；单任务最多 600 秒。
5. 成功写 `task_done`；失败写 `task_failed`。
6. 若错误包含 credits、quota、rate limit、余额不足等特征，冷却 6 小时，期间不再调用模型。

默认每日最多 40 次模型任务、累计运行 7200 秒。可在 `node/config.json` 调整。第三方模型服务没有统一的剩余额度 API，因此系统不能伪造“精确余额”；本地闸门与错误冷却确保不会在已知额度不足后连续执行。

History 任务每 5 分钟写入 `history/snapshots-YYYY-MM-DD.jsonl`，任务事件写入 `history/tasks-YYYY-MM-DD.jsonl`，默认保留 30 天。记录不包含飞书消息正文、模型答案、token 或 App Secret。

如需多个工作目录，在 Hub agent 中配置 `workspaces` 别名、Node 中配置相同目录的 `allowed_work_dirs`，详见 [手机端控制设计](mobile-control-design.md)。飞书 `/trash <相对路径>` 只把普通路径移动到工作区内可恢复目录；隐藏路径、项目指令/版本文件及 Windows 保留名不可暂存。只读工具的待审批请求可由飞书 `/approve <短码>` 或 `/reject <短码>` 处理；OpenCode 原生 shell/edit 权限始终被拒绝。配置发送者白名单后，可用 `/edit <相对文本文件>: <要求>` 生成单文件草案并审批完整差异；Windows 实机仍需验证。

## 运维

```powershell
.\scripts\windows\status.ps1
.\scripts\windows\verify.ps1
Get-ScheduledTask -TaskName "AgentControl-*"
Get-Content .\logs\node.err.log -Tail 50
Get-Content .\logs\hub.err.log -Tail 50
```

停止并移除自启任务（保留配置、数据库、日志和历史）：

```powershell
.\scripts\windows\uninstall.ps1
```

若 OpenCode 报额度不足，先在提供商后台补充额度或等待重置，再删除 `runtime/budget.json` 中的冷却时间；不要在未解决提供商额度前反复重启 Node。
