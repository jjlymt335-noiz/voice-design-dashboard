$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-ExecutionPolicy Bypass -File "C:\Users\江嘉骝\voice-design-dashboard\run_push.ps1"' -WorkingDirectory 'C:\Users\江嘉骝\voice-design-dashboard'
$trigger = New-ScheduledTaskTrigger -Daily -At '17:00'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U
Register-ScheduledTask -TaskName 'VoiceDesignDailyReport' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Voice Design daily report - 17:00 push to Lark' -Force
Write-Host 'OK - Task created successfully'
