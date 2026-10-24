# 飞书 PDF 附件

## 使用

先通过 `/workspace use <别名>` 选择工作区，再发送 **PDF 文件消息**，等机器人回复提取结果后发送“总结这份 PDF”等普通文字要求。当前仅支持 PDF，不包括图片消息、Word 或云文档链接。

同一个应用、发送者、聊天、工作区内，最新 PDF 关联有效 24 小时；新上传替换当前关联。`/pdf clear` 取消关联但保留文件与已有任务。控制命令（包括 `/edit`）不自动携带 PDF。任务入队时冻结附件 ID，切换目录或上传新文件不改变旧任务。

上传会保存文件并提取文字，不调用模型；后续普通文字任务才会把提取内容发送给该 Agent 已配置的模型服务。文档内容作为不可信资料，不授予执行、编辑或删除权限；修改仍走 `/edit` 完整差异审批。

## 安装与 OCR（macOS / Windows）

使用运行 Node 的同一 Python：

```bash
python3 -m pip install -r node/requirements.txt
```

Windows 在安装器创建的 venv 中运行 `.venv\Scripts\python.exe -m pip install -r node\requirements.txt`。文字 PDF 仅需 PyMuPDF。扫描页使用内置 Tesseract OCR 支持，另外需要语言数据，默认 `eng+chi_sim`（英文和简体中文）。

Windows 新安装时可给 `scripts/windows/install.ps1` 加 `-InstallPdfOcr`，自动下载官方中英文语言包到本机运行目录。

可安装 Tesseract 和对应语言包，或把官方 [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) 的 `eng.traineddata`、`chi_sim.traineddata` 放入项目 `runtime/tessdata/`。程序也寻找 Homebrew 标准目录及 Windows `C:/Program Files/Tesseract-OCR/tessdata`。自定义位置通过本机 `node/config.json` 指定：

```json
"pdf": {"ocr": true, "language": "eng+chi_sim", "tessdata": "C:/OCR/tessdata"}
```

Mac 路径可设 `/opt/homebrew/share/tessdata`。仅英文可设 `language: "eng"`；`ocr: false` 禁用扫描识别。缺少任一指定语言包时明确标记未识别页，不静默退回仅英文。补装后下次处理会重试不完整提取；修改 Node 配置需重启 Node。

PyMuPDF 使用 AGPL / 商业双重许可，对外分发前需确认所选许可要求。参考 [官方 OCR 文档](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html) 和 [项目许可](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING)。

## 限制与本机数据

- 须配置 `allowed_open_ids`，机器人须具备消息资源读取权限；缺权限时下载失败会提示检查配置。
- 每个文件上限 10 MiB、50 页；最多 OCR 10 页，单页最多约 1200 万渲染像素，解析子进程超时 120 秒。加密文件需先解密。文本最多 60000 字符，未识别或截断页会在回执及模型上下文中明确标记。
- Hub 在 SQLite `attachments` 表保存原附件及来源范围，总容量 200 MiB；达到上限后须在电脑端归档维护。24 小时只代表关联失效，不自动删除文件。
- Node 保存到工作区 `.agent-control-files/<随机附件ID>/document.pdf` 与 `extracted.json`，不采用上传文件名作为路径；拒绝 symlink / Windows junction，下载校验 SHA-256。设备 token 只可获取已领取、仍运行中、来源匹配的任务附件。
- 附件和提取结果属于私人本机数据，项目忽略并禁止发布。其他工作区也应忽略 `.agent-control-files/`。数据库备份也包含附件。

升级须一起重启 Hub、Node。Windows 包包含同源实现，仍需 Windows 实机及真实手机附件验收。
