"""Dashboard 自动更新: 拉取数据 + git commit + push"""
import subprocess
import sys
import os
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))

def run(cmd, check=True):
    print(f"  > {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip())
    if check and r.returncode != 0:
        print(f"  ERROR: exit code {r.returncode}")
    return r.returncode

def main():
    print(f"=== Auto Update {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 1. fetch data
    print("\n[1/4] Fetching data...")
    rc = run(f'"{sys.executable}" fetch_data.py')
    if rc != 0:
        return 1

    # 2. git add + commit
    print("\n[2/4] Committing...")
    run("git add data/dashboard_data.json", check=False)
    msg = f"chore: auto-update dashboard data {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    rc = run(f'git commit -m "{msg}"', check=False)

    # 3. git pull rebase
    print("\n[3/4] Pulling...")
    run("git pull --rebase origin master", check=False)

    # 4. git push
    print("\n[4/4] Pushing...")
    rc = run("git push origin master")
    if rc != 0:
        return 1

    print("\n=== Done ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
