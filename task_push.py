"""定时任务包装器：运行 push_to_lark.py 并记录日志"""
import subprocess
import sys
import os
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_push.txt")

with open(log_file, "w", encoding="utf-8") as f:
    f.write(f"=== Task run at {datetime.now()} ===\n")
    f.write(f"Python: {sys.executable}\n")
    f.write(f"CWD: {os.getcwd()}\n\n")

    result = subprocess.run(
        [sys.executable, "push_to_lark.py"],
        capture_output=True, text=True, timeout=120
    )

    f.write("--- STDOUT ---\n")
    f.write(result.stdout)
    f.write("\n--- STDERR ---\n")
    f.write(result.stderr)
    f.write(f"\n--- Exit code: {result.returncode} ---\n")

sys.exit(result.returncode)
