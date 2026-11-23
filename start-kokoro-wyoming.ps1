# Start the KokoClone/Kokoro Wyoming TTS server inside WSL on port 10222.
# Safe to call repeatedly: leaves an already-listening server alone.
#
# WSL localhost forwarding can be off, so readiness is checked against
# KOKORO_HOST (the WSL Tailscale IP) rather than Windows netstat on 127.0.0.1.
$ErrorActionPreference='Stop'
$root='C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2'
$port=10222
$log=Join-Path $root 'kokoro-wyoming.log'
$err=Join-Path $root 'kokoro-wyoming.err'
$host_ip='100.113.130.123'
$envLine=Get-Content (Join-Path $root '.env') -ErrorAction SilentlyContinue |
  Where-Object { $_ -match '^KOKORO_HOST=' } | Select-Object -First 1
if($envLine -and ($envLine -replace '^KOKORO_HOST=','').Trim()){
  $host_ip=($envLine -replace '^KOKORO_HOST=','').Trim()
}
function Test-Kokoro([string]$h,[int]$p){
  try { return [bool](Test-NetConnection -ComputerName $h -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue) }
  catch { return $false }
}
if(Test-Kokoro $host_ip $port){ "Kokoro already listening on ${host_ip}:$port"; exit 0 }
$py='"/mnt/c/Users/Geggais and Rick/kokoclone/venv/bin/python"'
$script='"/mnt/c/Users/Geggais and Rick/Documents/Codex/olive_assist_v2/tools/kokoro/run_kokoro_wyoming.py"'
$p=Start-Process -FilePath 'wsl.exe' -ArgumentList @('-e',$py,$script) `
  -PassThru -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden
"started WSL pid $($p.Id)"
$deadline=(Get-Date).AddSeconds(180)
while((Get-Date) -lt $deadline){
  if(Test-Kokoro $host_ip $port){ "Kokoro listening on ${host_ip}:$port"; exit 0 }
  Start-Sleep -Seconds 2
}
"TIMEOUT waiting for ${host_ip}:$port; check $err"
exit 1
