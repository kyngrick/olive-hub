# Start the local TTS stack used by the LibreEcho speaker, detached.
#   :10201  Piper English (en_GB-alan-medium)
#   :10202  Piper Italian  (it_IT-serena-medium)
#   :10200  Wyoming language router the speaker actually talks to
# Safe to call repeatedly: a service whose port is already listening is left alone.
$ErrorActionPreference='Stop'
$root = 'C:\Users\Geggais and Rick\Documents\Codex\piper-wyoming'
$py = Join-Path $root '.venv\Scripts\python.exe'
$progress = 'C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\restart-progress.log'
# Change these to any installed Piper voice, e.g. it_IT-serena-high or
# it_IT-riccardo-x_low. Download with:
#   .\.venv\Scripts\python.exe -m piper.download_voices <voice> --download-dir data
$enVoice = 'en_GB-alan-medium'
$itVoice = 'it_IT-serena-medium'
if (-not (Test-Path $py)) { throw "piper venv not found: $py" }
function Get-Env([string]$name) {
  $p = 'C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.env'
  foreach($l in Get-Content -LiteralPath $p){ if($l -match ('^'+[regex]::Escape($name)+'=(.*)$')){ return $Matches[1].Trim() } }
  return ''
}
$routerPort = [int](Get-Env 'TTS_ROUTER_PORT')
if ($routerPort -le 0) { $routerPort = 10200 }

function Step([string]$message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  tts: {1}' -f (Get-Date), $message
    Add-Content -Path $progress -Value $line -Encoding utf8
}
function Test-Listening([int]$port) {
    $rows = & netstat.exe -ano -p TCP 2>$null | Select-String -Pattern 'LISTENING\s+\d+\s*$'
    foreach ($row in $rows) {
        $parts = ($row.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 5 -and $parts[1] -match ":$port`$") { return $true }
    }
    return $false
}
function Start-Detached([string]$name, [string[]]$arguments, [string]$logName) {
    $out = Join-Path $root "$logName.log"
    $err = Join-Path $root "$logName.err"
    $result = Start-Process -FilePath $py -ArgumentList $arguments `
      -WorkingDirectory $root -RedirectStandardOutput $out `
      -RedirectStandardError $err -WindowStyle Hidden -PassThru
    Step "started $name pid $($result.Id)"
}

if (-not (Test-Listening 10201)) {
    Start-Detached 'english piper' @(
        'run_piper_windows.py','--voice',$enVoice,
        '--uri','tcp://127.0.0.1:10201','--data-dir','data','--download-dir','data'
    ) 'piper-en'
} else { Step 'english piper already listening' }

if (-not (Test-Listening 10202)) {
    Start-Detached 'italian piper' @(
        'run_piper_windows.py','--voice',$itVoice,
        '--uri','tcp://127.0.0.1:10202','--data-dir','data','--download-dir','data'
    ) 'piper-it'
} else { Step 'italian piper already listening' }

if (-not (Test-Listening $routerPort)) {
    Start-Detached 'TTS language router' @(
        'wyoming_tts_lang_router.py','--host','0.0.0.0','--port',"$routerPort",
        '--en-voice',$enVoice,'--it-voice',$itVoice,
        '--persona-file','"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\voice_personas.json"',
        '--kokoro-port','10222',
        '--kokoro-host',(Get-Env 'KOKORO_HOST')
    ) 'tts-router'
} else { Step 'TTS router already listening' }

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    if ((Test-Listening $routerPort) -and (Test-Listening 10201) -and (Test-Listening 10202)) {
        Step 'TTS stack ready'; exit 0
    }
    Start-Sleep -Seconds 1
}
Step 'TIMEOUT waiting for TTS ports; check piper-*.err / tts-router.err'
exit 1


