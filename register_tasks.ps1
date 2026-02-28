# Register both scheduled tasks
# C:\vdd -> 项目目录 (避免中文路径)
# C:\pybin -> Python 目录 (避免中文路径)

# Task 1: Lark push (17:00 daily)
$action1 = New-ScheduledTaskAction -Execute 'C:\pybin\python.exe' -Argument 'task_push.py' -WorkingDirectory 'C:\vdd'
$trigger1 = New-ScheduledTaskTrigger -Daily -At '17:00'
$settings1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal1 = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
Register-ScheduledTask -TaskName 'VoiceDesignDailyReport' -Action $action1 -Trigger $trigger1 -Settings $settings1 -Principal $principal1 -Description 'Voice Design daily report push to Lark' -Force
Write-Host 'Lark push task registered'

# Task 2: Dashboard update (16:00 daily)
$action2 = New-ScheduledTaskAction -Execute 'C:\pybin\python.exe' -Argument 'task_update.py' -WorkingDirectory 'C:\vdd'
$trigger2 = New-ScheduledTaskTrigger -Daily -At '16:00'
$settings2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal2 = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
Register-ScheduledTask -TaskName 'VoiceDesignDashboardUpdate' -Action $action2 -Trigger $trigger2 -Settings $settings2 -Principal $principal2 -Description 'Voice Design Dashboard fetch data and git push' -Force
Write-Host 'Dashboard update task registered'

Write-Host "`nDone!"
