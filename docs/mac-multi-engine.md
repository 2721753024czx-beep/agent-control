# Mac 多引擎验证：OpenCode 与 Codex 并存

本版提供显式开启的 Mac Codex app-server 适配器。默认 OpenCode 部署入口保留；Claude Code 和 Windows 原生引擎尚未接入。本机适配通过不等于手机验收通过。

## 实例与路由

同一 Node 可承接不同飞书应用映射的 Agent：

| Agent | engine_type | exec_path | 工作区 |
| --- | --- | --- | --- |
| 个人 OpenCode | serve | http://127.0.0.1:4096 | 独立个人项目 |
| 团队 Codex | serve | codex://local | 独立团队项目 |

`codex://local` 是固定适配器标识，不是远程地址或可执行命令。二者共用设备级持久队列；待审批也占用设备，不能抢占。每任务创建新 Codex 会话；续接上下文来自 Node 的工作区记忆快照和冻结的 PDF，不能继承桌面当前对话。

## 本机配置

先验证 Codex 本机登录可用。此适配器精确适配 `codex-cli 0.164.0-alpha.11.1`，其他版本停止执行，需复测后更新版本门槛。不要自动替换已有客户端。

在设备空闲并暂停手机提交时备份本地 Hub/Node 配置。Node 添加：

```json
"codex": {
  "enabled": true,
  "executable": "/absolute/path/to/codex"
}
```

为 Codex 使用独立飞书应用或经明确确认切换专用测试应用；配置其 `engine_type: "serve"`、`exec_path: "codex://local"`，以及独立工作区、owner、成员角色和白名单。更新 Node `allowed_work_dirs` 及已有 `agent_work_dirs`；不同逻辑 Agent 的根目录不可重叠。保留 OpenCode 实例和原工作区。

运行 `scripts/doctor.py` 检查配置；`--live` 仅检查现有 HTTP 服务，不能证明 Codex 模型通过。新代码通过回归后，确认空闲再定向重载 Hub/Node，不重装或停止 OpenCode 服务。

## 安全和能力边界

- Codex 实际 cwd 是空临时目录，随机命名权限配置档仅允许该目录和平台最小运行路径只读，工具网络关闭。项目内容只能由 Node 受控接口传入。[官方权限配置档](https://learn.chatgpt.com/docs/permissions)
- 逐项禁用用户配置的 MCP 和插件并复核结果；禁用 hooks、shell、浏览器、电脑操作、图片读取/生成和子代理等能力。保留该版本执行 dynamic tool 必需的本地 code-mode host；不能把它当 shell 使用。
- 模型可请求的项目工具目前仅 `ac_read`：相对非隐藏路径、普通 UTF-8 文本、最大32KiB；拒绝绝对路径、`..`、软/硬链接、FIFO和二进制。每次实际读取走原有本人一次性审批；拒绝不提供内容，未知请求停止任务。
- `/edit` 由模型生成提案，再由 Node 展示差异、本人审批、备份并写入；`/trash` 仍由 Node 单次审批后可恢复移动。不会开放 Codex 原生修改或命令审批。
- `/skills`、`/memory` 等 Node 控制命令、PDF提取与记忆注入继续共用；不宣称原生技能自动执行、目录搜索或图像工具已经支持。
- 正常完成、错误或超时后须停止任务 app-server；无法确认停止则退出 Node，保留 Hub 执行占用，管理员核实后恢复，不强行开始下一任务。

这些是应用层及指定版本沙箱约束，不保证本机其他进程不能修改文件，也不宣称所有 Codex 内部工具均经独立渗透验证。

## 复现与手机验收

```bash
python3 -m unittest discover -s tests -q
python3 e2e_test.py
python3 scripts/verify_codex_adapter.py --live
python3 scripts/verify_codex_adapter.py --live --checks boundaries
```

最后一项需已安装依赖和 Codex 登录，最多四次真实模型调用；只用合成数据及临时数据库。验证实际读取/Hub审批、PDF/记忆、跨引擎排队路由、读取拒绝及修改拒绝/批准备份；飞书发送是替身，OpenCode排队项仅验证路由，不调用其模型。测试数据不写入现役库。

手机在 Codex 测试机器人中依次读取测试文件并拒绝/批准，尝试其他成员代批，再测试 `/edit` 和 `/queue`。同时在原 OpenCode 机器人确认仍能处理任务。接着按部署验收表测越界和多人角色，不能把上面的隔离验证当作手机实测结果。

回退时空闲后只恢复该测试 Agent 的 OpenCode exec_path 和显示名，关闭 Node codex 开关并定向重载；保留审批、任务、文件和备份记录。配置、身份、认证和日志始终留本机。
