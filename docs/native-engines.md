# Claude Code / Codex 原生引擎接入验证

本轮以 v2026.10.27.1 为基线。探针是本机测试工具，不是手机任务执行适配器；当前生产引擎仍为受限 OpenCode。不能仅修改 `exec_path` 或开放 CLI 就认为完成接入。

## 当前实测

测试在 Mac 隔离临时目录执行，不使用飞书应用配置、私人文件或现役工作区，不重启现役服务。

| 项目 | 结果 |
| --- | --- |
| Claude Code 版本 | 2.1.138，非交互 JSON 与禁用工具选项可用 |
| Claude 模型调用 | 隔离配置返回“Not logged in”；加载用户配置复测60秒超时，无成功答案。auth status 有 OAuth 记录，不能据此判定登录失效或模型可用 |
| Codex 版本 | 0.164.0-alpha.11.1，app-server stdio initialize 握手通过 |
| Codex 模型调用 | 独立只读 exec 收到精确测试标记，退出0，未观察到工具事件 |
| 原生文件审批、越界、取消/超时回收、多人飞书链路 | 尚未接入、尚未验收 |
| Windows 原生引擎 | 尚未实测 |

Codex 模型冒烟不是 app-server 模型轮次或工具安全证明。只读沙箱也不能保证每次读取都经过手机批准，不能直接复用为远程入口。测试中本机不支持 `--disable apply_patch`，已移除该错误参数，不把该工具宣称为禁用。

## 接入方案

```text
飞书 → Hub（身份/队列/审批）→ Node（目录/角色/PDF/记忆/审计）
                                      ↓ 引擎适配接口
                  OpenCode serve / Claude Agent SDK / Codex app-server
```

- Hub 的应用、用户、聊天及工作区绑定和设备 FIFO 继续共用。Node 保留 PDF 提取、记忆快照、差异审批后写入和可恢复清理，引擎只产出答案或修改提案。
- **Claude Code**：使用官方 Agent SDK 的 `canUseTool`，结合明确 ask/deny 规则或 PreToolUse hook。读取必须在实际执行前通过 Node 路径校验和已有一次性审批；未知工具、Bash、原生 Edit/Write、子代理、外部目录默认拒绝。启动时控制项目/用户 hooks、MCP 和插件，避免从本机配置引入额外能力。CLI 的 tools 参数只用于当前无工具测试，不能当完整审批适配。[官方权限与回调说明](https://code.claude.com/docs/en/agent-sdk/permissions)
- **Codex**：使用官方 app-server 的 JSON-RPC 事件、请求ID、thread/turn/item 绑定和取消接口。原生命令/文件变更审批不能无条件接受；安全工具策略尚需验证。可探索将只读文件能力封成 Node 提供的 dynamic tool/MCP，并验证禁用或隔离其余本机工具，不能仅依赖提示词。dynamicTools 当前是实验接口，须按目标版本生成 schema 并测试。[官方协议说明](https://learn.chatgpt.com/docs/app-server)
- 审批发起者、聊天、任务、有效期及角色继续由 Hub 校验；拒绝/过期必须回传原生引擎，停止该动作。超时/取消只有确认子进程或 turn 停止后才释放设备占用；失败不静默降级到 CLI 或另一模型。

## 可复现测试

```bash
python3 scripts/probe_native_engines.py
python3 scripts/probe_native_engines.py --engine codex --live
python3 scripts/probe_native_engines.py --engine claude --live
python3 scripts/probe_native_engines.py --engine claude --live --claude-user-settings
```

默认只查版本及 Mac/POSIX 上 Codex 握手；`--live` 会消耗本机账号额度。Claude 默认不加载用户/项目设置，若认证或供应商配置依赖用户设置，审阅后使用 `--claude-user-settings`；仍禁用 hooks、工具、MCP、技能和 Chrome。只输出分类结果，不输出认证内容或模型日志。新环境先安装并验证客户端能正常问答，不复制他人凭据。Windows `.cmd/.bat/.ps1` shim 暂不在探针支持范围，不经过 shell 执行。

## 下一阶段验收门槛

1. Claude 先解决本机模型认证；两端分别完成结构化答案、PDF/记忆上下文、编辑提案测试。
2. 对实际工具调用验证读取批准/拒绝/过期、外部路径与链接拒绝、未知/写入/shell工具拒绝；不能以模型自称拒绝代替工具证据。
3. 在隔离 Hub/Node 中测试多人权限、他人代批、串行队列、超时/重连不重执行、审批期间不释放占用。
4. 建独立飞书测试 Agent，完成手机和 Windows 实机验收后才允许生产配置选择新引擎。安装脚本、doctor、数据库引擎枚举和发布包随后一同更新。
