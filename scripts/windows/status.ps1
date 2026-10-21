$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Write-Host "Agent Control scheduled tasks" -ForegroundColor Cyan
Get-ScheduledTask -TaskName "AgentControl-*" | Select-Object TaskName, State | Format-Table
Write-Host "Hub health" -ForegroundColor Cyan
try { Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5 | ConvertTo-Json }
catch { Write-Host $_ -ForegroundColor Red }
Write-Host "Recent logs" -ForegroundColor Cyan
foreach ($name in "hub.err.log", "node.err.log", "history.err.log") {
    $path = Join-Path $Root "logs\$name"
    if (Test-Path $path) { Write-Host "--- $name"; Get-Content $path -Tail 10 }
}
Write-Host "Recent history" -ForegroundColor Cyan
Get-ChildItem (Join-Path $Root "history") -Filter "*.jsonl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 2 |
    ForEach-Object { Write-Host "--- $($_.Name)"; Get-Content $_.FullName -Tail 5 }
