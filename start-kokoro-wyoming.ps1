# Start the KokoClone/Kokoro Wyoming TTS server inside WSL on port 10222.
# Safe to call repeatedly: leaves an already-listening server alone.
$ErrorActionPreference='Stop'
$root='C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2'
$port=10222
$log=Join-Path $root 'kokoro-wyoming.log'
$err=Join-Path $root 'kokoro-wyoming.err'
function Test-Listening([int]$p){
  $rows=& netstat.exe -ano -p TCP 2>$null | Select-String -Pattern 'LISTENING\s+\d+\s*$'
  foreach($row in $rows){ $parts=($row.Line -split '\s+')|Where-Object{$_}; if($parts.Count -ge 5 -and $parts[1] -match ":$p`$"){return $true} }
  return $false
}
if(Test-Listening $port){ "Kokoro already listening on $port"; exit 0 }
$py='"/mnt/c/Users/Geggais and Rick/kokoclone/venv/bin/python"'
$script='"/mnt/c/Users/Geggais and Rick/Documents/Codex/olive_assist_v2/tools/kokoro/run_kokoro_wyoming.py"'
$p=Start-Process -FilePath 'wsl.exe' -ArgumentList @('-e',$py,$script) `
  -PassThru -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden
"started WSL pid $($p.Id)"
$deadline=(Get-Date).AddSeconds(180)
while((Get-Date) -lt $deadline){
  if(Test-Listening $port){ "Kokoro listening on $port"; exit 0 }
  Start-Sleep -Seconds 2
}
"TIMEOUT waiting for $port; check $err"
exit 1
