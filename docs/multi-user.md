# 个人 Agent 与团队 Agent

## 默认个人独占

每个逻辑 Agent 仍对应一个独立飞书机器人 App ID。同一设备的多个 Agent 可以共用受限 OpenCode 服务，但须使用互不包含、互不重叠的项目目录；每次任务创建新模型会话，按目录读取项目记忆。它们不是新增操作系统账户或容器，不代表操作系统级隔离。

`hub/config.json` 的单个 Agent 配置：

```json
{
  "app_id": "cli_personal_example",
  "device_id": "pc1",
  "agent_name": "opencode",
  "display_name": "个人 Agent",
  "engine_type": "serve",
  "exec_path": "http://127.0.0.1:4096",
  "work_dir": "/Users/example/projects/alice",
  "access_mode": "personal",
  "owner_open_id": "ou_alice",
  "allowed_open_ids": ["ou_alice"],
  "app_secret": "replace-with-your-app-secret"
}
```

个人 Agent 只允许所有者私聊使用。旧配置仅有一个白名单用户时，自动兼容为其个人 Agent；没有白名单或存在多个用户却未声明所有者时拒绝访问，绝不采用“第一个发消息的人自动成为所有者”。个人模式中即使白名单增加其他用户，也不会授予其控制权。

同一电脑给另一人配置独立 Agent：在 `agents` 中新增另一份配置，使用不同 `app_id`、所有者及目录（例如 `/Users/example/projects/bob`），设备 ID 可以相同。不要复用个人项目目录来模拟共享。

## 显式团队共享

共同维护一个项目时使用一个 `shared` Agent。沿用上面字段，并修改：

```json
"access_mode": "shared",
"owner_open_id": "ou_alice",
"allowed_open_ids": ["ou_alice", "ou_bob", "ou_carol"],
"member_roles": {"ou_carol": "editor"}
```

- 所有者始终为 editor；未指定角色的其他成员为 reader。
- reader 可提交问答、读取项目共享记忆、发送 PDF 并提出处理要求；不能使用 `/edit` 或 `/trash`。
- editor 可发起受控修改与可恢复清理；每个任务的审批仍只属于发起者，所有者不能代批别人的任务。
- `/trash <相对路径>` 从 v2026.10.26.1 起也必须单次卡片审批：展示目标及指纹，拒绝或过期保持原位，批准后复核快照再移动至 `.agent-control-trash/`。目录扫描最多2000项、64MiB；电脑端恢复时须核对 manifest，不能覆盖已存在的原路径。
- 团队成员共享项目文件与 `.agent-memory`，不要放个人秘密。PDF 自动关联、工作区选择仍按用户隔离。团队群聊可用，但回复与审批卡片会对群成员可见。
- `/history` 只展示该用户在当前 Agent 的任务；`/queue` 展示数量和忙闲状态，不显示别人的请求内容。每用户最多 5 项待处理任务，每 Agent 最多 20 项。

Windows 的目录可写成 `C:/Projects/alice`、`C:/Projects/team`。新安装器要求 `-OwnerOpenId ou_...` 或交互输入，不会默认开放给所有飞书用户。

## 调度与不可抢占

当前一个 Node 串行执行整台设备的任务，多个逻辑 Agent 也排同一设备 FIFO。这样既不会打断同一 Agent，也避免共用执行服务时重入。它不代表各 Agent 已支持并行执行。

执行中包含等待审批阶段。Hub 以数据库事务领取任务并保存 `execution_active` 占用，重复 Node / 并发轮询不能领取第二项；新消息只入队。普通用户没有 `/stop`、`/cancel` 或抢占命令。

Hub 的超时状态不会立刻释放执行占用：必须等 Node 确认执行已结束并上报，才允许下一项。Node 自身任务时限、发起者拒绝和审批过期仍可能结束当前任务。网络上报失败时只重试结果，不重复执行任务。Hub 重启保留占用；Node 崩溃后宁可等待恢复，不自动抢占可能仍在运行的旧会话。

排队任务领取时再次检查当前成员权限及工作目录；已撤销授权或目录已移除的任务拒绝执行。正在执行的任务不会因新请求或配置变化自动终止。

## 配置检查与恢复

```bash
python3 hub/manage.py validate-config --config hub/config.json
```

修改访问策略、机器人或目录配置后，需要在队列空闲时重启 Hub。不要在任务执行中重启 Node。Node `allowed_work_dirs` 要包含各项目目录；可额外配置逐 Agent 白名单：

```json
"agent_work_dirs": {
  "cli_personal_example": ["/Users/example/projects/alice"],
  "cli_team_example": ["/Users/example/projects/team"]
}
```

配置此字段后，未列出的 Agent / 目录会由 Node 再次拒绝。模型服务仍沿用禁止 shell、原生编辑与外部目录的规则。

若 Node 崩溃导致占用残留，本机管理员须先停止该设备的 Node **及其 OpenCode 服务**，确认旧任务不再执行，再运行下面的本地管理命令并重新启动服务；不能将此命令当作正常抢占方式，不开放飞书远程恢复接口：

```bash
python3 hub/manage.py release-device pc1 --confirm-worker-stopped
```

Mac / Windows 使用同源策略与数据库逻辑；真实多账号、多机器人及 Windows 实机需单独验收。
