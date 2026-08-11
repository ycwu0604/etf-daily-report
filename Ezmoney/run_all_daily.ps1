<#
.SYNOPSIS
    Unified ETF Daily Report - Scheduled Task Wrapper
.DESCRIPTION
    Runs the full pipeline:
      1. Ezmoney: download xlsx → parse into SQLite → generate Ezmoney report
      2. Capital: API fetch → generate Capital report
      3. Combined: Ezmoney DB + Capital API → generate combined 4-ETF report
    
    Called by Windows Task Scheduler at 09:00 and 18:00.
#>

$ErrorActionPreference = "Continue"

# ── Fix console encoding ──────────────────────────────────────────────
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ── Start OA Proxy if available ──────────────────────────────────────
$pxwExe = Join-Path $env:LOCALAPPDATA "PEGAAi Opencode\opencode_proxy\pxw.exe"
if (Test-Path $pxwExe) {
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
$etfBase   = "C:\Users\YC_WU\Desktop\Side_Project\ETF"
$ezmoneyDir = Join-Path $etfBase "Ezmoney"
$capitalDir = Join-Path $etfBase "Capital"
$logDir    = Join-Path $etfBase "reports\logs"

# ── Ensure directories exist ──────────────────────────────────────────
@($logDir, (Join-Path $etfBase "reports")) | ForEach-Object {
    if (-not (Test-Path $_)) { New-Item -ItemType Directory -Path $_ -Force | Out-Null }
}

# ── Logging ────────────────────────────────────────────────────────────
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logFile = Join-Path $logDir "run_all_$timestamp.log"

function Write-Log($msg) {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Write-Log "=== Unified ETF Daily Report Start ==="

# ═══════════════════════════════════════════════════════════════════════
# Step 1: Ezmoney — download + parse + report
# ═══════════════════════════════════════════════════════════════════════
Write-Log "--- Step 1: Ezmoney (download + parse + report) ---"
$ezmoneyScript = Join-Path $ezmoneyDir "run_daily.py"
$output = & $pythonExe $ezmoneyScript 2>&1
$exitCode = $LASTEXITCODE
$output | ForEach-Object { Write-Log "  $_" }
if ($exitCode -ne 0) {
    Write-Log "  [WARN] Ezmoney step failed (exit=$exitCode), continuing..."
} else {
    Write-Log "  Ezmoney done."
}

# ═══════════════════════════════════════════════════════════════════════
# Step 2: Capital — API fetch + report
# ═══════════════════════════════════════════════════════════════════════
Write-Log "--- Step 2: Capital (API + report) ---"
$capitalScript = Join-Path $capitalDir "gen_capital_report.py"
$output = & $pythonExe $capitalScript 2>&1
$exitCode = $LASTEXITCODE
$output | ForEach-Object { Write-Log "  $_" }
if ($exitCode -ne 0) {
    Write-Log "  [WARN] Capital step failed (exit=$exitCode), continuing..."
} else {
    Write-Log "  Capital done."
}

# ═══════════════════════════════════════════════════════════════════════
# Step 3: Combined report (Ezmoney DB + Capital API)
# ═══════════════════════════════════════════════════════════════════════
Write-Log "--- Step 3: Combined report ---"
$combinedScript = Join-Path $etfBase "gen_combined_report.py"
$output = & $pythonExe $combinedScript 2>&1
$exitCode = $LASTEXITCODE
$output | ForEach-Object { Write-Log "  $_" }
if ($exitCode -ne 0) {
    Write-Log "  [ERROR] Combined report failed (exit=$exitCode)"
} else {
    Write-Log "  Combined report done."
}

# ═══════════════════════════════════════════════════════════════════════
# Cleanup old logs (keep 30 days)
# ═══════════════════════════════════════════════════════════════════════
$cutoff = (Get-Date).AddDays(-30)
Get-ChildItem -Path $logDir -Filter "run_all_*.log" |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }

Write-Log "=== Unified ETF Daily Report Complete ==="
