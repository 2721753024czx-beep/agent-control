# Windows 新电脑安装

先读 [Agent执行入口](DEPLOY-AGENT.md)。本入口针对原生Windows当前用户登录会话，不等于WSL安装或Windows系统服务；定时任务API可能需要本机管理员批准。

## 准备

- 安装Python 3.9+，确保PowerShell中 `python --version` 正确且不是商店占位启动器。
- 安装兼容的OpenCode **1.19.0** 并配置可用模型。先检查现有版本，不替换V2；安装方式参照 [官方文档](https://opencode.ai/docs/)。npm shim仅在安装器能定位真实Node脚本时支持，否则停下改用官方原生可执行文件。
- 把Windows发布包解压到普通持久本地目录，例如 `C:\AgentControl\agent-control`，工作目录例如 `C:\AgentProjects\personal`。避免OneDrive同步、临时目录或受控文件夹。
- 飞书 [配置与Open ID登记](deploy-feishu.md) 由用户完成。Secret不进聊天；不复制Mac配置、数据库、身份或模型登录。一个应用不同时在两台Hub消费。

## 身份登记（如尚无Open ID）

从仓库根运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r hub\requirements.txt -r node\requirements.txt
.\.venv\Scripts\python.exe scripts\enroll_identity.py --app-id cli_REPLACE
```

按终端口令从目标飞书账号私聊机器人，人工核对候选身份，命令退出后再运行安装器。

## 首次安装

工作目录必须先存在。审阅本仓库脚本后，仅对这次进程绕过执行策略，不改变系统全局策略：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\install.ps1 `
  -WorkDir "C:\AgentProjects\personal" -AppId cli_REPLACE `
  -OwnerOpenId ou_REPLACE -ConfirmExclusiveFeishuApp -SkipLarkCliBind
```

交互隐藏输入App Secret。安装器会运行一次模型冒烟测试（有耗时/费用），若暂不允许调用可加 `-SkipOpenCodeSmokeTest`，但必须将模型链路标为未验收。扫描OCR可选 `-InstallPdfOcr`，会从Tesseract官方仓库下载中英文数据；不需新图像模型API。

安装器只允许新部署：已有配置/数据库/同名定时任务就退出，不覆盖或删除旧部署。凭据ACL限制当前用户与SYSTEM。注册OpenCode、Hub、Node、History四个登录自启任务；安装失败可能保留已生成的配置或部分任务，先按维护指南核查，不直接重跑或清空目录。

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py --live
powershell -NoProfile -File scripts\windows\verify.ps1
```

按 [验收表](deploy-acceptance.md) 完成手机测试，另须实际注销/登录或重启验证自启。尚未提供Windows实机通过证明；计划任务Ready和健康返回成功不能替代真实手机、模型、OCR或重启验收。

## 团队与维护

默认个人独占。需团队或多个逻辑Agent时，在设备空闲后按 [多人指南](multi-user.md) 修改本机Hub/Node配置，逐人验证角色；不要借“增加应用协作者”替代使用授权。群聊只对shared模式开放。

具体升级、备份及移除定时任务见 [维护指南](deploy-maintenance.md)。Windows停止定时任务后仍须确认OpenCode子进程已退出，不以“任务已停止”假定所有子进程都停止。不要结束其他项目的OpenCode进程。
