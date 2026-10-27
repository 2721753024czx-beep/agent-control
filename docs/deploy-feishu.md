# 飞书应用与身份准备

在 [飞书开发者后台](https://open.feishu.cn/app) 创建企业自建应用并开启机器人。App Secret 在本机终端隐藏输入，不能发给 Agent 聊天；暴露过则先重置。第二位普通用户不需要应用协作者权限。

## 权限与订阅

| 项目 | 配置 |
|---|---|
| 单聊事件 | `im:message.p2p_msg:readonly` |
| 机器人回复 | `im:message:send_as_bot`；回复接口若明确要求额外消息权限，按后台接口权限提示最小补充，不批量开所有权限 |
| 群里 @ | 需要群聊才开 `im:message.group_at_msg:readonly`；不要开全部群消息权限 |
| PDF资源下载 | `im:resource` |
| 事件配置 | 长连接，接收消息 v2.0：`im.message.receive_v1` |
| 回调配置 | 长连接，`card.action.trigger`，用于审批按钮 |

配置后创建版本、指定可用范围、发布并等待管理员审核。首次先仅所有者私聊。个人 Agent 按设计拒绝群聊；要测试群里 @，还须明确配置 shared 和成员白名单，并把应用机器人加入内部测试群。普通用户不因加入群聊就有控制权。

## 获取可信 Open ID，解决首次连接问题

Open ID 是应用相关身份，不能复用另一机器人下的值。准备好 venv 和 Hub 依赖后，在仓库根执行（Windows将解释器换成 `.venv\Scripts\python.exe`）：

```bash
.venv/bin/python scripts/enroll_identity.py --app-id cli_REPLACE
```

输入 Secret，按终端随机口令让目标用户**私聊新机器人**；五分钟内精确匹配后仅输出候选 Open ID，不执行任何任务。人工核实发送者后记录到本机配置。每位成员重复登记，不把候选自动当所有者。命令结束后再启 Hub，不并行运行两个消费者。

若飞书要求先建立连接才能保存事件，先启动该登记命令，再保存长连接订阅/发布；若五分钟不足，退出后重新运行。首次无响应先检查机器人能力、可用范围、发布状态、订阅是否成功，不通过扩大白名单解决。

机器人只记录已注册处理器；不要额外订阅“消息已读”等本项目未处理事件。lark-cli只是可选调试工具，Hub运行不依赖它，不需要为安装强制登录lark-cli。

跨组织用户不是此次默认部署范围；需额外对外共享、认证和审核，参照 [飞书官方说明](https://open.feishu.cn/document/develop-robots/add-bot-to-external-group?lang=zh-CN)。消息能力以 [官方消息文档](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1) 为准。
