# Repository Guidelines

本仓库实现通过 Hub、Node 与飞书 IM 远程调度和控制本机 Agent 的系统。

## 项目结构

`hub/` 接收飞书消息、排队并回复；`node/` 拉取任务、执行受限 OpenCode 并写审计；`scripts/` 放 macOS launchd 与 Windows 安装脚本；`tests/` 是单元测试，`e2e_test.py` 是隔离链路测试。设计与部署说明在 `docs/`。`hub/config.json`、`node/config.json`、数据库、日志、`.agent-memory/`、`.agent-control-edits/` 和 `.agent-control-trash/` 是本机数据，不上传 GitHub。

## 开发与测试

在仓库根运行 `python3 -m unittest discover -s tests -q` 检查模块行为，运行 `python3 e2e_test.py` 验证隔离的 Hub→Node→回复链路。Windows 发布包可由 `python3 scripts/build_windows_bundle.py` 生成；安装步骤见 `docs/windows-opencode-feishu.md`。不要用真实飞书配置运行测试。

## 风格与安全

Python 使用 4 空格缩进、`snake_case` 函数与模块名；测试命名为 `tests/test_*.py`。手机普通任务必须走受限 `serve`；不得把 CLI、shell、OpenCode 原生编辑或未知工具加入远程审批白名单。单文件修改只走 Node 的 `/edit` 草案、完整差异审批和备份；清理只走可恢复的 `/trash`，不能直接删除。

## 版本、提交与 PR

本仓库在首次备份前无 Git 历史可归纳。今后每次推送前更新 `VERSION` 和 `CHANGELOG.md`：明确“本版完成”“相对上版替换/迭代”“验证与未完成”。提交建议 `release: vYYYY.MM.DD.N`，同名标签，并在 PR 描述中附测试结果、关联问题及飞书交互变化的截图。推送前运行 `python3 scripts/check_release.py`，确保不含凭据、运行数据或个人记忆。

## 共享记忆强制约定（shared-memory）

任何 agent 在改动 / 任务完成前后，必须读 / 写 `.agent-memory/`：

1. **进入目录先读**：`.agent-memory/index.md` → `requirements.md` → `context.md`（关键决策再看 `decisions.md`）。
2. **改动 / 任务完成前回写**：用 shared-memory 技能把关键信息写入对应文件：

   ```bash
   bash ~/.agents/skills/shared-memory/scripts/memory.sh append <文件> <标题>
   ```

   回写内容：需求变化、关键上下文 / 中间结论 / 产物位置、关键决策与原因。
3. **目的**：其他 agent 进入目录可先读记忆，防止并发出错与重复劳动；也便于用户回溯上下文。具体用法见 `/shared-memory` 技能。
