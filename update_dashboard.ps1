# Dashboard 自动更新脚本
Set-Location "C:\Users\江嘉骝\voice-design-dashboard"

Write-Host "=== Voice Design Dashboard Auto Update ==="
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# 1. 运行数据获取脚本
Write-Host "`n[1/3] Fetching latest data..."
& "C:\Users\江嘉骝\AppData\Local\Python\bin\python.exe" fetch_data.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: fetch_data.py failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

# 2. Git add & commit
Write-Host "`n[2/3] Committing changes..."
git add data/dashboard_data.json
$commitMsg = "chore: auto-update dashboard data $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git commit -m "$commitMsg`n`nCo-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"

if ($LASTEXITCODE -eq 0) {
    Write-Host "Changes committed successfully" -ForegroundColor Green
} else {
    Write-Host "No changes to commit or commit failed" -ForegroundColor Yellow
}

# 3. Git pull (rebase) & push
Write-Host "`n[3/4] Pulling latest changes..."
git pull --rebase origin master
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Pull failed, attempting to continue..." -ForegroundColor Yellow
}

Write-Host "`n[4/4] Pushing to GitHub..."
git push origin master
if ($LASTEXITCODE -eq 0) {
    Write-Host "Pushed successfully" -ForegroundColor Green
} else {
    Write-Host "ERROR: Push failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== Update completed successfully ===" -ForegroundColor Green
