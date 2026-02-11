# 包装脚本 - 确保正确的工作目录和环境
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

# 运行 Python 脚本
python push_to_lark.py

