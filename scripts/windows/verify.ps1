[CmdletBinding()]
param(
    [ValidateRange(0, 1800)]
    [int]$WaitForFeishuSeconds = 0,
    [switch]$RunOpenCodeSmokeTest
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Failures = 0

function Pass([string]$Message) {
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Fail([string]$Message) {
    $script:Failures += 1
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

Write-Host "==> Agent Control Windows verification" -ForegroundColor Cyan

foreach ($relative in ".venv\Scripts\python.exe", "hub\config.json", "node\config.json") {
    if (Test-Path (Join-Path $Root $relative)) { Pass "$relative exists" }
    else { Fail "$relative missing" }
}

foreach ($name in "AgentControl-OpenCode", "AgentControl-Hub", "AgentControl-Node", "AgentControl-History") {
    try {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        if ($task.State -in "Ready", "Running") { Pass "$name state=$($task.State)" }
        else { Fail "$name state=$($task.State)" }
    } catch {
        Fail "$name is not registered"
    }
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
    if ($health.ok -and [int]$health.agents -ge 1) { Pass "Hub health ok; agents=$($health.agents)" }
    else { Fail "Hub health response is incomplete" }
} catch {
    Fail "Hub health request failed: $($_.Exception.Message)"
}

try {
    $hubConfig = Get-Content (Join-Path $Root "hub\config.json") -Raw | ConvertFrom-Json
    $nodeConfig = Get-Content (Join-Path $Root "node\config.json") -Raw | ConvertFrom-Json
    if ($hubConfig.agents.Count -eq 1 -and $hubConfig.agents[0].app_id -like "cli_*") {
        Pass "one Feishu agent is configured"
    } else { Fail "expected exactly one valid Feishu agent" }
    if ($nodeConfig.hub_url -eq "http://127.0.0.1:8000") { Pass "Node uses loopback Hub" }
    else { Fail "Node hub_url is not loopback" }
    if (Test-Path $hubConfig.agents[0].work_dir) { Pass "OpenCode work directory exists" }
    else { Fail "OpenCode work directory does not exist" }
    if ($hubConfig.agents[0].engine_type -eq "serve" -and $nodeConfig.serve_password.Length -ge 32) {
        $Basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("opencode:$($nodeConfig.serve_password)"))
        $OpenCodeHealth = Invoke-RestMethod -Uri "http://127.0.0.1:4096/global/health" -Headers @{ Authorization = "Basic $Basic" } -TimeoutSec 10
        if ($OpenCodeHealth.healthy) { Pass "protected OpenCode server is healthy" }
        else { Fail "protected OpenCode server is unhealthy" }
    } else { Fail "safe serve engine is not configured" }
} catch {
    Fail "configuration validation failed: $($_.Exception.Message)"
}

$snapshot = Get-ChildItem (Join-Path $Root "history") -Filter "snapshots-*.jsonl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($snapshot -and $snapshot.LastWriteTime -gt (Get-Date).AddMinutes(-15)) {
    Pass "recent health snapshot exists"
} else { Fail "no health snapshot from the last 15 minutes" }

if ($RunOpenCodeSmokeTest) {
    try {
        $smoke = & opencode run "只回复 WINDOWS_OPENCODE_VERIFY_OK，不要执行文件操作。" 2>&1
        if ($LASTEXITCODE -eq 0 -and (($smoke -join "`n") -match "WINDOWS_OPENCODE_VERIFY_OK")) {
            Pass "OpenCode live smoke test"
        } else { Fail "OpenCode live smoke test did not return marker" }
    } catch { Fail "OpenCode live smoke test failed: $($_.Exception.Message)" }
}

if ($WaitForFeishuSeconds -gt 0) {
    $started = [DateTime]::UtcNow
    $deadline = (Get-Date).AddSeconds($WaitForFeishuSeconds)
    Write-Host "请现在用手机飞书向机器人发送 /status；等待最多 $WaitForFeishuSeconds 秒。" -ForegroundColor Yellow
    $found = $false
    while ((Get-Date) -lt $deadline -and -not $found) {
        foreach ($file in Get-ChildItem (Join-Path $Root "history") -Filter "tasks-*.jsonl" -ErrorAction SilentlyContinue) {
            foreach ($line in Get-Content $file.FullName -Tail 100 -ErrorAction SilentlyContinue) {
                try {
                    $event = $line | ConvertFrom-Json
                    $eventTime = [DateTime]::Parse($event.timestamp).ToUniversalTime()
                    if ($eventTime -ge $started -and $event.event -eq "control" -and $event.command -eq "/status") {
                        $found = $true
                    }
                } catch { }
            }
        }
        if (-not $found) { Start-Sleep -Seconds 2 }
    }
    if ($found) { Pass "phone Feishu -> Windows Hub -> Node /status path" }
    else { Fail "no new /status event arrived from Feishu within the timeout" }
}

if ($Failures -gt 0) {
    Write-Host "Verification failed: $Failures check(s)." -ForegroundColor Red
    exit 1
}
Write-Host "Verification passed." -ForegroundColor Green
exit 0
