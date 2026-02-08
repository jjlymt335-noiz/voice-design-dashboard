$action = New-ScheduledTaskAction -Execute 'C:\Users\江嘉骝\AppData\Local\Python\bin\python.exe' -Argument 'C:\Users\江嘉骝\voice-design-dashboard\push_to_lark.py' -WorkingDirectory 'C:\Users\江嘉骝\voice-design-dashboard'
$trigger = New-ScheduledTaskTrigger -Daily -At '17:00'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'VoiceDesignDailyReport' -Action $action -Trigger $trigger -Settings $settings -Description 'Voice Design daily report - 17:00 push to Lark' -Force
Write-Host 'OK - Task created successfully'
