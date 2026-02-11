# 设置 Dashboard 数据自动更新任务（每天16:00运行）
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-ExecutionPolicy Bypass -File "C:\Users\江嘉骝\voice-design-dashboard\update_dashboard.ps1"' -WorkingDirectory 'C:\Users\江嘉骝\voice-design-dashboard'
$trigger = New-ScheduledTaskTrigger -Daily -At '16:00'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U
Register-ScheduledTask -TaskName 'VoiceDesignDashboardUpdate' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Voice Design Dashboard auto-update - 16:00 fetch data and push to GitHub' -Force
Write-Host 'OK - Dashboard update task created successfully'
