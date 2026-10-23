# 飞书手机控制：工作区、审批与删除边界

## 参考与取舍

Orca 的手机端把电脑作为会话和状态的权威来源，手机查看运行状态、回复等待中的问题，并能在多个工作区之间切换。WorkBuddy 微信助理提供任务续接、审批命令与文件修改、查看结果及通知。agent-control 继续使用飞书，不要求安装独立手机应用；借鉴的是“电脑持有执行状态、手机处理待办”的交互模型。

## 已实现：工作目录选择

在 `hub/config.json` 的目标 agent 下设置 `workspaces`，别名由电脑端配置，飞书不接收任意绝对路径：

```json
"workspaces": {
  "research": "/Users/example/Research",
  "agent-control": "/Users/example/agent-control"
},
"allowed_open_ids": ["ou_你的飞书open_id"]
```

在对应 Node 的 `node/config.json` 设置同一批目录的绝对路径：

```json
"allowed_work_dirs": [
  "/Users/example/Research",
  "/Users/example/agent-control"
]
```

配置后重启 Hub 和 Node。飞书命令：`/workspace list`、`/workspace current`、`/workspace use research`。目录选择按 `App ID + 发送者 open_id` 保存。任务入队时把目录快照写入 SQLite，Node 启动任务前再次 canonicalize 并与本地白名单比对。旧任务不会因为之后切换目录而改变。配置目录不存在时不会进入列表。

## 审批链路（受限模式已实现）

Hub 建立一次性、五分钟有效的审批记录，绑定 task、session、permission、原发送者及原聊天。Node 的 `serve` 执行器轮询 OpenCode 的待决权限，并在原聊天回复含完整请求内容的审批卡片。手机可点「批准一次」或「拒绝」；回调还必须匹配原卡片消息 ID，重复点击、跨人/跨聊天、过期或任务结束均无效。`/approve <短码>` 和 `/reject <短码>` 保留为卡片回调不可用时的后备方式。Node 只向 OpenCode 回复 `once` 或 `reject`，不发送“始终允许”。OpenCode 原生的 shell、edit、子任务和外部目录权限始终被拒绝。

按钮需在飞书开发者后台「事件与回调 → 回调配置」选择**长连接**并订阅 `card.action.trigger`，保存、发布新应用版本；只配置 `im.message.receive_v1` 无法接收按钮点击。Hub 与消息事件共用出站 WebSocket，不需要公网回调 URL。启用后用无敏感数据的只读请求实测按钮；若卡片发送失败，Hub 自动改发原有文字审批提示。不要在真实卡片出现前假定回调已开通。

要启用该链路，Hub agent 的 `engine_type` 必须为 `serve`，`exec_path` 指向回环 OpenCode 地址；Node 配置 `serve_password`，并由 `scripts/run_safe_opencode.py` 启动 OpenCode。启动器使用 `--pure`，默认拒绝未知权限，并将 shell、编辑、子任务和外部目录权限设为 `deny`。Node 每次从服务器读取工具 ID，只显式禁用危险或未知工具；不能把只读工具设为 `true`，否则 OpenCode 1.19 的逐请求设置会覆盖服务端 `read: ask`，实测甚至覆盖 `read: deny`。服务器缺少默认拒绝、危险工具拒绝或 `read: ask` 时任务会失败关闭。macOS 已切换为受限 serve，但手机飞书审批尚未实测。

macOS 可先在 `node/config.json` 设置 `serve_password`（至少 32 字符）、`serve_port`（默认 4096）、`opencode_model`、`default_work_dir` 和 `allowed_work_dirs`，再运行 `bash scripts/install_safe_opencode_launchd.sh` 安装独立的回环服务。确认 `/config` 的拒绝规则后，把 Hub agent 改为 `serve` 并重启 Hub/Node。该脚本只重装 OpenCode 的 launchd 服务，不会触碰 Hub/Node。切换前备份本机配置；不要在文档或 Git 中记录服务密码。

## 删除的硬边界

手机端审批不能批准删除。仅在消息里写“不要删除”或配置 `rm *: deny` 不够：`python`、`git clean`、PowerShell、补丁工具等都可能删除。真正的保护需要单独的远程执行身份或沙箱，禁止其直接修改原始工作区；每个任务先创建可恢复快照，在临时工作副本执行并检查文件差异。若差异含删除，禁止提交到原目录。

已实现 `/trash <工作区相对路径>`：Node 先写恢复清单，再将指定文件或目录移动到同工作区的 `.agent-control-trash/<时间戳>-<随机ID>/`；不自动清空。路径穿越、空段、Windows 保留名/非法字符、隐藏路径、`AGENTS.md`/`SKILL.md`/版本文件、符号链接/junction、工作区根和暂存目录自身会被拒绝。路径统一使用 `/` 分隔。OpenCode 提示词告知 Agent 推荐这个命令；不受限的 OpenCode 工具仍能直接删除，因此手机普通任务一律拒绝 CLI。

受限 `serve` 模式逐请求禁用 shell、编辑、补丁、子任务和未知工具；服务端也默认拒绝这些能力。清理须通过单独的 `/trash` 控制命令移动文件；CLI 模式无条件禁止用于手机普通任务。

## 受控单文件编辑

飞书发送 `/edit notes.txt: 把标题改为项目进展`。仅配置了 `allowed_open_ids` 的发送者可入队。Node 只让受限模型生成指定文件的完整 UTF-8 文本草案；目标路径来自命令，不接受模型改写。Node 计算完整差异（最多 1800 字），以独立的 `safe_change` 类型显示在审批卡片；原发送者点「批准一次」或发送一次性 `/approve <短码>` 后才写入。拒绝、过期、文件在期间变化、模型草案格式不符或差异过大均不写入。

只支持现有目录下的单个文本文件（最多 32 KiB；扩展名 `.py`、`.md`、`.txt`、`.js`、`.jsx`、`.ts`、`.tsx`、`.css`、`.html`）。隐藏路径、凭据配置、`AGENTS.md` 等受保护文件、路径穿越及符号链接/junction 均被拒绝。原内容与恢复清单写入工作区 `.agent-control-edits/`，该目录仅留在本机。该流程不是对 OpenCode 原生 `edit` 工具的授权，不支持任意 shell 或批量改动；Windows 与真实手机链路仍需实机验收。

## 验收

1. 两个飞书用户选择不同别名，互不影响；旧任务目录保持快照。
2. 不在白名单的目录、失效目录和软链接逃逸均被拒绝。
3. 审批短码只能由原发送者使用一次；过期、重放和跨任务回复被拒绝。
4. `rm`、`git clean`、Python `unlink`、PowerShell `Remove-Item`、编辑器删除等尝试均不能删除原始文件；受控清理只产生可恢复移动和审计记录。
