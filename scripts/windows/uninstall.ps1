$ErrorActionPreference = "Continue"
foreach ($name in "AgentControl-OpenCode", "AgentControl-Hub", "AgentControl-Node", "AgentControl-History") {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Host "已移除计划任务；配置、数据库、日志和历史记录均保留。"
