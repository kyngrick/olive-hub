# Start the local WhatsApp bridge (Node/whatsapp-web.js) detached.
# Safe to call repeatedly: an already-listening bridge is left alone.
$ErrorActionPreference='Stop'
$root='C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2'
$bridge=Join-Path $root 'whatsapp-bridge'
$node='C:\Program Files\nodejs\node.exe'
$progress=Join-Path $root 'restart-progress.log'
$envFile=Join-Path $root '.env'
function Step([string]$msg){ $line='{0:yyyy-MM-dd HH:mm:ss}  whatsapp: {1}' -f (Get-Date),$msg; Add-Content -Path $progress -Value $line -Encoding utf8 }
function Get-Env([string]$name){
  foreach($l in Get-Content -LiteralPath $envFile){ if($l -match ('^'+[regex]::Escape($name)+'=(.*)$')){ return $Matches[1].Trim() } }
  return ''
}
function Test-Listening([int]$port){
  $rows=& netstat.exe -ano -p TCP 2>$null | Select-String -Pattern 'LISTENING\s+\d+\s*$'
  foreach($row in $rows){ $parts=($row.Line -split '\s+')|Where-Object{$_}; if($parts.Count -ge 5 -and $parts[1] -match ":$port`$"){return $true} }
  return $false
}
$portText=Get-Env 'WHATSAPP_BRIDGE_PORT'
if([string]::IsNullOrWhiteSpace($portText)){ $portText='3011' }
$port=[int]$portText
$sessionDir=Get-Env 'WHATSAPP_BRIDGE_SESSION'
if([string]::IsNullOrWhiteSpace($sessionDir)){ $sessionDir='session' }
if(Test-Listening $port){ Step "already listening on $port"; exit 0 }
if(-not (Test-Path $node)){ throw "node.exe not found: $node" }
$token=Get-Env 'WHATSAPP_BRIDGE_TOKEN'
if([string]::IsNullOrWhiteSpace($token)){ throw 'WHATSAPP_BRIDGE_TOKEN missing from .env' }

$server=Join-Path $bridge 'server.js'
$log=Join-Path $bridge 'bridge.log'
$err=Join-Path $bridge 'bridge.err'
# Start-Process inherits these variables; WMI process creation did not.
$env:BRIDGE_TOKEN=$token
$env:BRIDGE_PORT="$port"
$env:BRIDGE_SESSION_DIR=$sessionDir
$env:BRIDGE_HEADLESS='0'
$result=Start-Process -FilePath $node -ArgumentList "`"$server`"" `
  -WorkingDirectory $bridge -RedirectStandardOutput $log `
  -RedirectStandardError $err -WindowStyle Hidden -PassThru
Step "started pid $($result.Id)"
$deadline=(Get-Date).AddSeconds(40)
while((Get-Date) -lt $deadline){
  if(Test-Listening $port){ Step "bridge listening on $port"; exit 0 }
  Start-Sleep -Seconds 1
}
Step "TIMEOUT waiting for $port; check whatsapp-bridge\bridge.err"
exit 1
