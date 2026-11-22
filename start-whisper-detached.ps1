# Start the local Whisper Wyoming ASR server on port 10301, detached.
# Safe to call repeatedly: an already-listening server is never killed.
# Logs: whisper.log / whisper.err; progress is appended to restart-progress.log.
$env:TQDM_DISABLE = '1'   # keep whisper.err free of progress bars
# Multilingual "tiny" is ~4x faster than "base" on this CPU while still
# handling Italian commands; the hub's ASR timeout is 15 seconds.
$env:WHISPER_MODEL = 'tiny'
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'whisper_wyoming_server.py'
$marker = Join-Path $root 'whisper.starting'
$progress = Join-Path $root 'restart-progress.log'
if (-not (Test-Path $venv)) { throw "venv not found: $venv" }
if (-not (Test-Path $script)) { throw "Whisper server script not found: $script" }

function Step([string]$message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  whisper: {1}' -f (Get-Date), $message
    Add-Content -Path $progress -Value $line -Encoding utf8
}
function Is-Listening {
    $rows = & netstat.exe -ano -p TCP 2>$null | Select-String -Pattern 'LISTENING\s+\d+\s*$'
    foreach ($row in $rows) {
        $parts = ($row.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 5 -and $parts[1] -match ':10301$') { return $true }
    }
    return $false
}

if (Is-Listening) { Step 'already listening on 10301'; exit 0 }

# A previous starter may still be loading the model.  Do not race it.
if (Test-Path $marker) {
    $age = (Get-Date) - (Get-Item $marker).LastWriteTime
    if ($age.TotalSeconds -lt 120) { Step 'start already in progress'; exit 0 }
    Remove-Item $marker -Force -ErrorAction SilentlyContinue
}
Set-Content -Path $marker -Value (Get-Date -Format o) -Encoding ascii

try {
    $log = Join-Path $root 'whisper.log'
    $err = Join-Path $root 'whisper.err'
    foreach ($file in @($err, $log)) {
        if (Test-Path $file) {
            for ($i = 0; $i -lt 4; $i++) {
                try { Move-Item $file "$file.prev" -Force; break }
                catch { Start-Sleep -Milliseconds 300 }
            }
        }
    }

    # WMI-launched processes do not inherit $env, so pass the model explicitly.
    $inner = '"{0}" "{1}" --model tiny >> "{2}" 2>> "{3}"' -f $venv, $script, $log, $err
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine      = 'cmd.exe /c "' + $inner + '"'
        CurrentDirectory = $root
    }
    Step "started pid $($result.ProcessId)"

    # Model loading happens before the listener binds, so wait for the port.
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if (Is-Listening) { Step 'listening on 10301'; exit 0 }
        Start-Sleep -Seconds 1
    }
    Step 'TIMEOUT: 10301 did not open; check whisper.err'
    exit 1
} finally {
    Remove-Item $marker -Force -ErrorAction SilentlyContinue
}
