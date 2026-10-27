# Mac 新电脑安装

先读 [Agent执行入口](DEPLOY-AGENT.md)。使用持久目录，例如 `~/agent-control`；不要放 Desktop、Documents、Downloads、临时目录或只读卷。原生 Mac 登录会话通过 launchd 启动，注销/睡眠时不能承诺任务可用，不是24小时云服务。

## 1. 依赖与身份

安装 Python 3.9+（不假设系统自带可用Python），确认 `python3 --version`。按 [OpenCode官方安装指南](https://opencode.ai/docs/) 获取 **1.19.0** 并配置可用模型。本项目服务 API/权限适配针对该版本；不能因为命令也叫 `opencode` 就自动替换 V2。网络下载与模型登录由本机用户授权。

获取项目后，若要登记Open ID：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r hub/requirements.txt -r node/requirements.txt
.venv/bin/python scripts/enroll_identity.py --app-id cli_REPLACE
```

按 [飞书清单](deploy-feishu.md) 完成身份核对、事件和回调发布。先由本机交互OpenCode验证模型可回复；不要用拥有shell权限的CLI执行手机任务。

## 2. 首次配置

新建用户选择的项目目录，例如 `mkdir -p "$HOME/agent-project"`，再从仓库根运行（占位值必须替换）：

```bash
bash scripts/macos/install.sh \
  --workspace "$HOME/agent-project" \
  --app-id cli_REPLACE --owner-open-id ou_REPLACE \
  --confirm-exclusive-app
```

Secret提示时隐藏输入。生成本机随机token与密码，配置0600；检查已有数据则停止，不覆盖。项目记忆只创建缺失文件，不改写已有AGENTS.md；缺少共享记忆规则时请用户同意后补入，不能覆盖原指引。

若明确部署团队，可在以上命令增加 `--reader-open-id ou_MEMBER`（可重复）；这会显式启用shared。不要把所有人设editor；后续按多用户文档调整。不同逻辑Agent根目录不能互相包含。

## 3. 启动与验证

暂停手机提交，确认无任务后：

```bash
.venv/bin/python scripts/macos/services.py install --confirm-idle
.venv/bin/python scripts/doctor.py --live
.venv/bin/python scripts/macos/services.py status
```

注册 `com.agentcontrol.opencode/hub/node` 三个当前用户登录自启服务；Hub与OpenCode仅监听回环。新安装器不调用旧的install_launchd.sh，不全局pkill。若已有同名plist，停止并按维护指南处理，不覆盖另一个checkout。

后台无需保留终端或Codex会话。健康检查不代表模型额度可用或飞书链路已通过，须继续 [手机验收](deploy-acceptance.md)。日志可能包含会话鉴权URL，向他人提供前须脱敏。

## PDF扫描OCR与故障

文字PDF只需Node依赖中的PyMuPDF。扫描OCR还需可信来源的 `eng.traineddata`、`chi_sim.traineddata`；保存到本机 `runtime/tessdata/` 后，在node配置 `pdf.tessdata` 写绝对目录，见 [PDF指南](pdf-attachments.md)。缺失时如实提示，不把空文本当成功。打包不附带OCR数据或PyMuPDF二进制，正式商业分发须评估其AGPL/商业许可。

若launchd报权限错误，先检查项目位置和Python绝对路径。若端口已占用，不结束不明进程；确认所有者后选择迁移/停用旧服务。升级、恢复与卸载见 [维护指南](deploy-maintenance.md)。
