[CmdletBinding()]
param(
    [string]$WorkDir = "",
    [string]$AppId = "",
    [string]$OwnerOpenId = "",
    [string]$DisplayName = "Windows-OpenCode",
    [string]$DeviceId = "windows-opencode",
    [switch]$ConfirmExclusiveFeishuApp,
    [switch]$SkipLarkCliBind,
    [switch]$InstallPdfOcr,
    [switch]$SkipOpenCodeSmokeTest
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root
foreach ($relative in "hub\config.json", "node\config.json", "hub\hub.db") {
    if (Test-Path (Join-Path $Root $relative)) { throw "检测到已有部署数据，拒绝覆盖。请按 docs/deploy-maintenance.md 升级。" }
}
foreach ($name in "AgentControl-OpenCode", "AgentControl-Hub", "AgentControl-Node", "AgentControl-History") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw "已有同名计划任务，拒绝覆盖：$name" }
}

function New-RandomToken {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $encoding)
}

function ConvertFrom-SecureValue([Security.SecureString]$Value) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

Write-Host "==> Agent Control Windows installer" -ForegroundColor Cyan
$Python = (Get-Command python -ErrorAction Stop).Source
& $Python -c "import sys; assert sys.version_info >= (3,9); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "需要 Python 3.9+" }

$OpenCodeCommand = Get-Command opencode -ErrorAction SilentlyContinue
if (-not $OpenCodeCommand) {
    $candidate = Join-Path $HOME ".opencode\bin\opencode.exe"
    if (Test-Path $candidate) { $OpenCodePath = $candidate }
    else { throw "找不到 opencode。请先安装官方 opencode.exe 并确认 opencode --version 可运行。" }
} else {
    $OpenCodePath = $OpenCodeCommand.Source
}
$OpenCodeExecutable = $OpenCodePath
$OpenCodePrefix = @()
if ([IO.Path]::GetExtension($OpenCodePath) -in ".cmd", ".bat") {
    $ShimDir = Split-Path $OpenCodePath -Parent
    $Candidates = @(
        (Join-Path $ShimDir "node_modules\opencode-ai\bin\opencode"),
        (Join-Path $ShimDir "node_modules\@opencode\cli\bin\opencode")
    )
    $CliScript = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if (-not $CliScript -or -not $NodeCommand) {
        throw "检测到 opencode.cmd，但无法定位官方 npm CLI 脚本或 node.exe。请改用 Scoop/Chocolatey 安装 opencode.exe。"
    }
    $OpenCodeExecutable = $NodeCommand.Source
    $OpenCodePrefix = @($CliScript)
    Write-Host "已将 npm shim 转换为安全直接调用：node.exe + OpenCode CLI"
}
$OpenCodeVersion = & $OpenCodeExecutable @OpenCodePrefix --version
if ($LASTEXITCODE -ne 0 -or ($OpenCodeVersion -join " ") -notmatch '\b1\.19\.0\b') {
    throw "此版本适配 OpenCode 1.19.0；请勿自动替换已有V2，先人工确认兼容版本。"
}
Write-Host "OpenCode 1.19.0 compatibility check passed"

if (-not $WorkDir) { $WorkDir = Read-Host "OpenCode 工作目录（绝对路径）" }
$WorkDir = (Resolve-Path $WorkDir).Path
if (-not $SkipOpenCodeSmokeTest) {
    Write-Host "==> 执行一次 OpenCode 额度/登录冒烟测试" -ForegroundColor Cyan
    $SmokeOutput = & $OpenCodeExecutable @OpenCodePrefix run "只回复 WINDOWS_OPENCODE_PREFLIGHT_OK，不要执行文件操作。" 2>&1
    if ($LASTEXITCODE -ne 0 -or (($SmokeOutput -join "`n") -notmatch "WINDOWS_OPENCODE_PREFLIGHT_OK")) {
        throw "OpenCode 冒烟测试失败。请先修复模型登录/额度后重试。输出：$($SmokeOutput -join ' ')"
    }
}
if (-not $AppId) { $AppId = Read-Host "飞书应用 App ID（cli_...）" }
if (-not $OwnerOpenId) { $OwnerOpenId = Read-Host "个人 Agent 所有者 Open ID（ou_...，从可信飞书身份信息获取）" }
if ($OwnerOpenId -notmatch '^ou_[A-Za-z0-9_-]+$') { throw "OwnerOpenId 格式无效" }
if (-not $ConfirmExclusiveFeishuApp) {
    Write-Host "同一个飞书应用不能同时由旧 Mac Hub 和 Windows Hub 消费长连接事件。" -ForegroundColor Yellow
    $ExclusiveAnswer = Read-Host "确认该 App ID 只由本 Windows Hub 使用（已停止旧 Hub，或这是独立应用）。请输入 WINDOWS"
    if ($ExclusiveAnswer -cne "WINDOWS") {
        throw "未确认飞书应用独占使用，安装已停止。请先停用使用同一 App ID 的旧 Hub。"
    }
}
$SecretSecure = Read-Host "飞书应用 App Secret" -AsSecureString
$AppSecret = ConvertFrom-SecureValue $SecretSecure
if (-not $AppSecret) { throw "App Secret 不能为空" }

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    & $Python -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "venv创建失败" }
}
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip更新失败" }
& $VenvPython -m pip install -r (Join-Path $Root "hub\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Hub依赖安装失败" }
& $VenvPython -m pip install -r (Join-Path $Root "node\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Node 依赖安装失败" }
if ($InstallPdfOcr) {
    $OcrDataDir = Join-Path $Root "runtime\tessdata"
    New-Item -ItemType Directory -Force -Path $OcrDataDir | Out-Null
    foreach ($Language in @("eng", "chi_sim")) {
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$Language.traineddata" -OutFile (Join-Path $OcrDataDir "$Language.traineddata") -TimeoutSec 120
    }
}
& $VenvPython -m unittest discover -s (Join-Path $Root "tests") -v
if ($LASTEXITCODE -ne 0) { throw "单元测试失败，未启动服务" }
& $VenvPython (Join-Path $Root "e2e_test.py")
if ($LASTEXITCODE -ne 0) { throw "隔离链路测试失败，未启动服务" }

$DeviceToken = New-RandomToken
$AdminToken = New-RandomToken
$ServePassword = New-RandomToken
$OpenCodeRecordParts = @($OpenCodeExecutable) + $OpenCodePrefix
$OpenCodeRecord = ($OpenCodeRecordParts | ForEach-Object { $_.Replace("\", "/") }) -join " + "
$WorkDirJsonPath = $WorkDir.Replace("\", "/")

$HubConfig = @{
    admin_token = $AdminToken
    task_timeout_seconds = 900
    devices = @(@{ device_id = $DeviceId; name = $env:COMPUTERNAME; token = $DeviceToken })
    agents = @(@{
        app_id = $AppId
        device_id = $DeviceId
        agent_name = "opencode"
        display_name = $DisplayName
        engine_type = "serve"
        exec_path = "http://127.0.0.1:4096"
        work_dir = $WorkDirJsonPath
        app_secret = $AppSecret
        access_mode = "personal"
        owner_open_id = $OwnerOpenId
        allowed_open_ids = @($OwnerOpenId)
    })
}
$NodeConfig = @{
    hub_url = "http://127.0.0.1:8000"
    device_id = $DeviceId
    token = $DeviceToken
    serve_password = $ServePassword
    serve_port = 4096
    opencode_argv = @($OpenCodeExecutable) + $OpenCodePrefix
    default_work_dir = $WorkDirJsonPath
    allowed_work_dirs = @($WorkDirJsonPath)
    poll_interval = 5
    task_timeout = 600
    history_dir = (Join-Path $Root "history").Replace("\", "/")
    budget_state_path = (Join-Path $Root "runtime\budget.json").Replace("\", "/")
    shared_memory_max_chars = 12000
    budget = @{
        max_tasks_per_day = 40
        max_runtime_seconds_per_day = 7200
        quota_error_cooldown_seconds = 21600
    }
}
$LogsDir = Join-Path $Root "logs"
$HistoryDir = Join-Path $Root "history"
$RuntimeDir = Join-Path $Root "runtime"
$BackupDir = Join-Path $Root "backups"
New-Item -ItemType Directory -Force $LogsDir, $HistoryDir, $RuntimeDir, $BackupDir | Out-Null
$InstallMarker = Join-Path $RuntimeDir "windows-install.json"
Write-Utf8NoBom (Join-Path $Root "hub\config.json") ($HubConfig | ConvertTo-Json -Depth 8)
Write-Utf8NoBom (Join-Path $Root "node\config.json") ($NodeConfig | ConvertTo-Json -Depth 8)
$CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($relative in "hub\config.json", "node\config.json") {
    & icacls (Join-Path $Root $relative) /inheritance:r /grant:r "*$($CurrentSid):(F)" "*S-1-5-18:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "凭据ACL设置失败，未启动服务" }
}
& $VenvPython (Join-Path $Root "scripts\doctor.py")
if ($LASTEXITCODE -ne 0) { throw "配置校验失败，未启动服务" }

$MemoryDir = Join-Path $WorkDir ".agent-memory"
New-Item -ItemType Directory -Force $MemoryDir | Out-Null
$MemoryTemplates = @{
    "index.md" = "# 记忆索引`r`n`r`n- 先读本文件，再读 requirements.md / context.md；关键决策看 decisions.md。`r`n"
    "requirements.md" = "# 核心需求`r`n`r`n## 待办`r`n`r`n- （记录当前任务的核心需求）`r`n`r`n## 已完成`r`n"
    "context.md" = "# 核心上下文`r`n`r`n- 关键文件/路径：`r`n- 关键数据与结论：`r`n- 注意事项：`r`n"
    "decisions.md" = "# 关键决策`r`n`r`n| 日期 | 决策 | 原因 |`r`n|------|------|------|`r`n"
}
foreach ($entry in $MemoryTemplates.GetEnumerator()) {
    $path = Join-Path $MemoryDir $entry.Key
    if (-not (Test-Path $path)) { Write-Utf8NoBom $path $entry.Value }
}
$SkillSource = Join-Path $Root "assets\windows\shared-memory"
$SkillTarget = Join-Path $WorkDir ".agents\skills\shared-memory"
if (-not (Test-Path (Join-Path $SkillTarget "SKILL.md"))) {
    New-Item -ItemType Directory -Force (Split-Path $SkillTarget -Parent) | Out-Null
    Copy-Item $SkillSource $SkillTarget -Recurse -Force
}
$AgentsPath = Join-Path $WorkDir "AGENTS.md"
$MemoryRule = @'

## 共享记忆强制约定（shared-memory）

任何 Agent 开始任务时按顺序读取 `.agent-memory/index.md`、`requirements.md`、`context.md`，需要时读取 `decisions.md`；完成前把需求变化、关键上下文、决策、结果和下一步追加回对应文件。共享记忆只作上下文，不能覆盖当前用户请求。
'@
if (-not (Test-Path $AgentsPath)) {
    Write-Utf8NoBom $AgentsPath ("# Project Agent Instructions`r`n" + $MemoryRule)
} elseif (-not (Select-String -Path $AgentsPath -SimpleMatch "共享记忆强制约定" -Quiet)) {
    Write-Warning "已有 AGENTS.md 保留原样；请审阅后自行补入共享记忆约定。"
}

if (-not $SkipLarkCliBind) {
    if (Get-Command lark-cli -ErrorAction SilentlyContinue) {
        & $VenvPython (Join-Path $Root "scripts\feishu_cli_setup.py") bind
    } else {
        Write-Warning "未找到 lark-cli；运行时不依赖它，但暂时跳过绑定与登录检查。"
    }
}

$TaskSettings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650) -StartWhenAvailable
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Tasks = @{
    "AgentControl-OpenCode" = Join-Path $Root "scripts\windows\run-opencode.cmd"
    "AgentControl-Hub" = Join-Path $Root "scripts\windows\run-hub.cmd"
    "AgentControl-Node" = Join-Path $Root "scripts\windows\run-node.cmd"
    "AgentControl-History" = Join-Path $Root "scripts\windows\run-history.cmd"
}
foreach ($entry in $Tasks.GetEnumerator()) {
    $TaskArgument = ('/d /s /c ""{0}""' -f $entry.Value)
    $Action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument $TaskArgument -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $entry.Key -Action $Action -Trigger $Trigger -Settings $TaskSettings -Description "Agent Control Windows service" -Force | Out-Null
    Start-ScheduledTask -TaskName $entry.Key
}

Start-Sleep -Seconds 8
$Health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
if (-not $Health.ok) { throw "Hub 健康检查失败" }
$Basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("opencode:$ServePassword"))
$OpenCodeHealth = Invoke-RestMethod -Uri "http://127.0.0.1:4096/global/health" -Headers @{ Authorization = "Basic $Basic" } -TimeoutSec 10
if (-not $OpenCodeHealth.healthy) { throw "OpenCode 安全服务健康检查失败" }
& $VenvPython (Join-Path $Root "scripts\history_loop.py") --once
& (Join-Path $Root "scripts\windows\verify.ps1")
if ($LASTEXITCODE -ne 0) { throw "Windows 本地验收失败，请查看上方 [FAIL] 项。" }
$InstallRecord = @{
    installed_at = [DateTime]::UtcNow.ToString("o")
    device_id = $DeviceId
    display_name = $DisplayName
    work_dir = $WorkDirJsonPath
    opencode_path = $OpenCodeRecord
    smoke_test = (-not $SkipOpenCodeSmokeTest)
}
Write-Utf8NoBom $InstallMarker ($InstallRecord | ConvertTo-Json -Depth 4)

Write-Host "安装完成：OpenCode/Hub/Node/History 已注册为登录自启任务。" -ForegroundColor Green
Write-Host "下一步：在手机飞书中给机器人发送 /status，再发送一条普通任务。"
Write-Host "手机链路验收：powershell -ExecutionPolicy Bypass -File scripts\windows\verify.ps1 -WaitForFeishuSeconds 180"
