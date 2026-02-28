# 包装脚本 - 确保正确的工作目录和环境
$logFile = "C:\Users\江嘉骝\voice-design-dashboard\task_run_push.log"
$ErrorActionPreference = "Continue"

try {
    "=== Task run at $(Get-Date) ===" | Out-File $logFile -Append
    "Current directory: $PWD" | Out-File $logFile -Append

    Set-Location "C:\Users\江嘉骝\voice-design-dashboard"
    "Changed to: $PWD" | Out-File $logFile -Append

    # 运行 Python 脚本（使用完整路径）
    & "C:\Users\江嘉骝\AppData\Local\Python\bin\python.exe" push_to_lark.py 2>&1 | Out-File $logFile -Append

    "Exit code: $LASTEXITCODE" | Out-File $logFile -Append
} catch {
    "ERROR: $_" | Out-File $logFile -Append
}

