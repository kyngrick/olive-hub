# Olive hub watchdog: keep it running and apply a requested restart.
#
# Safe to run every few minutes:
#   * never kills or restarts a healthy hub unless a restart was requested
#   * a restart is requested by creating restart.request next to this script
#   * only invokes start-hub-detached.ps1 when the port is free or a restart
#     was requested
#   * appends a short line to hub-watchdog.log
#
# Used by the "Olive Hub" scheduled task (startup + every 5 minutes).
# Because that task runs as the same user (S4U), it can stop a hub that an
# interactive shell is not allowed to touch.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$log = Join-Path $root 'hub-watchdog.log'
$restartMarker = Join-Path $root 'restart.request'

function Write-Watchdog([string]$message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $message
    Add-Content -Path $log -Value $line -Encoding utf8
}
function Test-Listening([int]$port) {
    $rows = & netstat.exe -ano -p TCP 2>$null | Select-String -Pattern 'LISTENING\s+\d+\s*$'
    foreach ($row in $rows) {
        $parts = ($row.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 5 -and $parts[1] -match ":$port`$") { return $true }
    }
    return $false
}

try {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) {
        Move-Item $log "$log.prev" -Force
    }

    if (Test-Path $restartMarker) {
        Remove-Item $restartMarker -Force -ErrorAction SilentlyContinue
        Write-Watchdog 'restart requested; recycling the hub'
        $output = & (Join-Path $root 'start-hub-detached.ps1') 2>&1 | Out-String
        if ($output.Trim()) { Add-Content -Path $log -Value $output.TrimEnd() -Encoding utf8 }
        Write-Watchdog 'restart finished'
        exit 0
    }

    # The hub's ASR proxy needs the local Whisper server; start it if it died.
    if (-not (Test-Listening 10301)) {
        Write-Watchdog 'Whisper ASR is down; starting'
        $asrOutput = & (Join-Path $root 'start-whisper-detached.ps1') 2>&1 | Out-String
        if ($asrOutput.Trim()) { Add-Content -Path $log -Value $asrOutput.TrimEnd() -Encoding utf8 }
        Write-Watchdog 'Whisper start script finished'
    }

    # The speaker's TTS router (10200) and its two Piper backends need to be up.
    if (-not (Test-Listening 10200)) {
        Write-Watchdog 'TTS stack is down; starting'
        $ttsOutput = & (Join-Path $root 'start-tts-detached.ps1') 2>&1 | Out-String
        if ($ttsOutput.Trim()) { Add-Content -Path $log -Value $ttsOutput.TrimEnd() -Encoding utf8 }
        Write-Watchdog 'TTS start script finished'
    }

    # Local dashboard (DASH_PORT in .env, default 8767).
    if (-not (Test-Listening 8767)) {
        Write-Watchdog 'Dashboard is down; starting'
        $dashOutput = & (Join-Path $root 'start-dashboard-detached.ps1') 2>&1 | Out-String
        if ($dashOutput.Trim()) { Add-Content -Path $log -Value $dashOutput.TrimEnd() -Encoding utf8 }
        Write-Watchdog 'Dashboard start script finished'
    }

    # WhatsApp bridge (needs a one-time QR scan by the user).
    # Keep this port in sync with WHATSAPP_BRIDGE_PORT / WHATSAPP_BRIDGE_URL in .env.
    if (-not (Test-Listening 3014)) {
        Write-Watchdog 'WhatsApp bridge is down; starting'
        $waOutput = & (Join-Path $root 'start-whatsapp-bridge.ps1') 2>&1 | Out-String
        if ($waOutput.Trim()) { Add-Content -Path $log -Value $waOutput.TrimEnd() -Encoding utf8 }
        Write-Watchdog 'WhatsApp start script finished'
    }

    $listening = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue
    if ($listening) {
        exit 0                      # healthy: do nothing
    }

    Write-Watchdog 'hub is down; starting'
    $output = & (Join-Path $root 'start-hub-detached.ps1') 2>&1 | Out-String
    if ($output.Trim()) { Add-Content -Path $log -Value $output.TrimEnd() -Encoding utf8 }
    Write-Watchdog 'start script finished'
    exit 0
} catch {
    Write-Watchdog ('ERROR: ' + $_.Exception.Message)
    exit 1
}
