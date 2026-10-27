# 维护、更新与卸载

安装器仅用于首次部署。发现已有配置、数据库或同名服务时应停止，不能删除数据来绕过检查。

## 停机与备份

1. 暂停所有手机提交。通过 `/queue` 并结合本机数据库确认无 pending、running 或 `execution_active=1`；待审批也必须结束。任务超时不代表执行器已停止。
2. Mac 在原安装目录执行 `.venv/bin/python scripts/macos/services.py stop --confirm-idle`。Windows 先核对计划任务的操作路径属于当前安装，再依次停止 `AgentControl-Node`、`AgentControl-Hub`、`AgentControl-OpenCode` 及可选 History；还须确认这些任务的子进程已退出。不要结束其他项目的进程。
3. 在受保护的本地目录备份配置、数据库、工作区、附件、共享记忆和恢复暂存区。这些数据不能上传 GitHub。确认所有数据库写入进程停止后才能复制数据库及配套 WAL/SHM 文件；在线备份应使用 SQLite backup API。

如果执行器仍活动，先等完成或在本机调查，不强行清除数据库执行占用。

## 更新与恢复运行

- 查看目标标签的 CHANGELOG，保留当前源码版本和本地数据备份。
- 在独立目录获取新版本，先运行单测和隔离 E2E，再审阅配置迁移说明。将源码更新至原安装路径；保留配置、数据库、工作区，不执行丢弃本地文件的 Git 重置操作。首次安装器不能充当升级器。
- 使用原安装的虚拟环境安装必要依赖，运行静态诊断。修改访问目录、身份、角色或服务地址时重新确认授权范围。
- Mac 的 stop 保留 plist。登录用户终端依次执行 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentcontrol.opencode.plist`，再以相同命令启动 `com.agentcontrol.hub.plist`、`com.agentcontrol.node.plist`。仅首次注册使用 `services.py install --confirm-idle`。
- Windows 按 OpenCode、Hub、Node、可选 History 顺序启动已核对的任务。运行 `scripts/doctor.py --live`，再完成手机文本和审批冒烟测试。

失败时停止新服务，恢复旧源码和必要的成套数据备份，核对执行占用后再启动。不能把旧数据库覆盖到运行中的服务；没有通用自动回滚功能。

## 卸载与排障

Mac 空闲并暂停提交后执行 `.venv/bin/python scripts/macos/services.py uninstall --confirm-idle`，只移除属于本目录的注册，保留数据。Windows 核对任务操作路径后手动停止并移除这些任务；不要对未知同名任务运行旧卸载脚本。数据归档和飞书凭据撤销分别处理。

健康检查通过但飞书无回复时，核对已发布版本、可用范围、消息权限、事件/卡片回调、白名单和本应用 open_id，以及是否有另一 Hub 消费同一应用。线程启动日志不代表已连接。使用兼容 OpenCode V1 1.19.0，不自动覆盖已有 V2。日志可能含认证 URL，提供前先脱敏。
