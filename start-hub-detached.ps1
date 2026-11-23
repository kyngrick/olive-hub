# Start olive_hub.py fully detached (survives the calling shell).
# Use this from automation; start-hub.ps1 stays the interactive helper.
#
# Uses only native tools (netstat, taskkill) for the port dance: the WMI-based
# Get-NetTCPConnection/Stop-Process pair can block for minutes in session 0.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venv)) { throw "venv not found: $venv" }

$progress = Join-Path $root 'restart-progress.log'
function Step([string]$message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $message
    Add-Content -Path $progress -Value $line -Encoding utf8
}

function Listening-Pids {
    # -ano appends the owning PID after LISTENING, so anchor on both.
    $rows = & netstat.exe -ano -p TCP 2>$null | Select-String -Pattern 'LISTENING\s+\d+\s*$'
    foreach ($row in $rows) {
        $parts = ($row.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -lt 5) { continue }
        $local = $parts[1]
        if ($local -match ':(8765|10300)$') { $parts[-1] }
    }
}

Step 'start requested'

$pids = @(Listening-Pids | Sort-Object -Unique)
foreach ($processId in $pids) {
    Step "stopping pid $processId"
    try {
        & taskkill.exe /F /PID $processId 2>&1 | ForEach-Object { Step "  $_" }
    } catch {
        Step "  taskkill failed for pid $processId : $($_.Exception.Message)"
    }
}

# Wait for the old process to release both ports.
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    if (-not (Listening-Pids)) { break }
    Start-Sleep -Milliseconds 400
}
$still = @(Listening-Pids | Sort-Object -Unique)
if ($still) {
    Step ('ports still in use by pid(s) ' + ($still -join ','))
    throw ('port still in use by pid(s) ' + ($still -join ',') + ' - cannot restart')
}
Step 'ports are free'
Start-Sleep -Milliseconds 600

$log = Join-Path $root 'olive_hub.log'
$err = Join-Path $root 'olive_hub.err'
foreach ($file in @($err, $log)) {
    if (-not (Test-Path $file)) { continue }
    for ($i = 0; $i -lt 5; $i++) {
        try {
            Move-Item $file "$file.prev" -Force
            break
        } catch {
            # A just-killed process can hold the handle for another second.
            Start-Sleep -Milliseconds 500
            if ($i -eq 4) { Step "could not rotate $file : $($_.Exception.Message)" }
        }
    }
}

$inner = '"{0}" "{1}" >> "{2}" 2>> "{3}"' -f $venv, (Join-Path $root 'olive_hub.py'), $log, $err
# Extra outer quotes: cmd /c needs them when the command itself starts quoted.
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = 'cmd.exe /c "' + $inner + '"'
    CurrentDirectory = $root
}
Step "started pid $($result.ProcessId)"
Start-Sleep -Seconds 8
if (Test-Path $err) { Get-Content $err -Tail 8 | ForEach-Object { Step "  $_" } }

# The speaker sends audio to 10300, which forwards to Whisper on 10301.
$asr = & (Join-Path $root 'start-whisper-detached.ps1') 2>&1 | Out-String
if ($asr.Trim()) { $asr.TrimEnd() -split "`r?`n" | ForEach-Object { Step "  $_" } }

# Speaker TTS: English + Italian Piper behind the language router.
$tts = & (Join-Path $root 'start-tts-detached.ps1') 2>&1 | Out-String
if ($tts.Trim()) { $tts.TrimEnd() -split "`r?`n" | ForEach-Object { Step "  $_" } }

# WhatsApp bridge (local only; QR scan is a one-time manual step).
$wa = & (Join-Path $root 'start-whatsapp-bridge.ps1') 2>&1 | Out-String
if ($wa.Trim()) { $wa.TrimEnd() -split "`r?`n" | ForEach-Object { Step "  $_" } }

# Local dashboard: recycle it on every hub start so code/config changes take
# effect. This helper runs with the hub's token, so it can stop the elevated
# dashboard process that the watchdog may have started.
$dashPid = $null
foreach ($row in (& netstat.exe -ano -p TCP 2>$null | Select-String 'LISTENING\s+\d+\s*$')) {
    $parts = ($row.Line -split '\s+') | Where-Object { $_ }
    if ($parts.Count -ge 5 -and $parts[1] -match ':8767$') { $dashPid = $parts[-1]; break }
}
if ($dashPid) {
    Step "stopping dashboard pid $dashPid"
    & taskkill.exe /F /PID $dashPid 2>&1 | ForEach-Object { Step "  $_" }
    Start-Sleep -Seconds 2
}
$dash = & (Join-Path $root 'start-dashboard-detached.ps1') 2>&1 | Out-String
if ($dash.Trim()) { $dash.TrimEnd() -split "`r?`n" | ForEach-Object { Step "  $_" } }
Step 'done'
