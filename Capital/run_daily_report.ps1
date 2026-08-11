<#
.SYNOPSIS
    Capital ETF Daily Report - Scheduled Task Wrapper
.DESCRIPTION
    Runs gen_capital_report.py (API mode) daily and logs the output.
    Called by Windows Task Scheduler at 18:00.
#>

$ErrorActionPreference = "Continue"

# ── Fix console encoding ──────────────────────────────────────────────
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ── Start OA Proxy if available ──────────────────────────────────────
$pxwExe = Join-Path $env:LOCALAPPDATA "PEGAAi Opencode\opencode_proxy\pxw.exe"
if (Test-Path $pxwExe) {
    # Kill existing pxw.exe to avoid port conflict
    Stop-Process -Name pxw -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Start-Process -FilePath $pxwExe -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# ── Inherit proxy env vars if saved ──────────────────────────────────
$proxyEnvFile = Join-Path $env:USERPROFILE ".config\opencode\scheduler-agent\proxy_env.json"
if ((Test-Path $proxyEnvFile) -and -not $env:HTTPS_PROXY) {
    try {
        $proxyEnv = [System.IO.File]::ReadAllText($proxyEnvFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($proxyEnv.HTTPS_PROXY) { $env:HTTPS_PROXY = $proxyEnv.HTTPS_PROXY }
        if ($proxyEnv.HTTP_PROXY)  { $env:HTTP_PROXY = $proxyEnv.HTTP_PROXY }
        if ($proxyEnv.NO_PROXY)    { $env:NO_PROXY = $proxyEnv.NO_PROXY }
    } catch { }
}

# ── Paths ─────────────────────────────────────────────────────────────
$pythonExe = "C:\Users\YC_WU\AppData\Local\Programs\Python\Python313\python.exe"
$scriptPath = "C:\Users\YC_WU\Desktop\Side_Project\ETF\Capital\gen_capital_report.py"
$logDir = "C:\Users\YC_WU\Desktop\Side_Project\ETF\Capital\reports\logs"

# ── Ensure log directory exists ───────────────────────────────────────
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# ── Run the report script ─────────────────────────────────────────────
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logFile = Join-Path $logDir "run_$timestamp.log"

Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Starting Capital ETF report generation..." | Tee-Object -FilePath $logFile
Write-Host "  Script: $scriptPath" | Tee-Object -FilePath $logFile -Append

$output = & $pythonExe $scriptPath 2>&1
$exitCode = $LASTEXITCODE

$output | Tee-Object -FilePath $logFile -Append

if ($exitCode -eq 0) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Report generated successfully." | Tee-Object -FilePath $logFile -Append
} else {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ERROR: Script exited with code $exitCode" | Tee-Object -FilePath $logFile -Append
}

# ── Rotate old logs (keep 30 days) ────────────────────────────────────
$cutoff = (Get-Date).AddDays(-30)
Get-ChildItem -Path $logDir -Filter "run_*.log" |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
