# Point the LibreEcho speaker at an Olive Hub endpoint.
#
#   .\switch-olive-endpoint.ps1 -Endpoint 192.168.1.86     # at home (LAN)
#   .\switch-olive-endpoint.ps1 -Endpoint 100.108.79.37    # travelling (Tailscale)
#   .\switch-olive-endpoint.ps1 -Endpoint olive.example.com -Port 8765
#
# Reads SPEAKER_URL / SPEAKER_USER / SPEAKER_PASSWORD / HUB_TOKEN from .env.
param(
  [Parameter(Mandatory=$true)][string]$Endpoint,
  [int]$Port = 8765,
  [int]$AsrPort = 10300,
  [switch]$WhatIfOnly
)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$envFile=Join-Path $root '.env'
function Get-Env([string]$name){
  foreach($l in Get-Content -LiteralPath $envFile){
    if($l -match ('^'+[regex]::Escape($name)+'=(.*)$')){ return $Matches[1].Trim().Trim('"').Trim("'") }
  }
  return ''
}
$speaker=(Get-Env 'SPEAKER_URL').TrimEnd('/')
if($speaker -match '/api/v1$'){ $speaker=$speaker -replace '/api/v1$','' }
$user=Get-Env 'SPEAKER_USER'
$pass=Get-Env 'SPEAKER_PASSWORD'
$token=Get-Env 'HUB_TOKEN'
if(-not $speaker -or -not $user -or -not $pass -or -not $token){ throw 'Speaker/HUB settings missing from .env' }

$base="$speaker/api/v1"
$csrf=(Invoke-RestMethod -UseBasicParsing -Uri "$base/config" -TimeoutSec 10).data.csrf_token
$login=Invoke-RestMethod -UseBasicParsing -Uri "$base/auth/login" -Method Post `
  -Headers @{'X-LibreEcho-CSRF'=$csrf} -ContentType 'application/json' `
  -Body (@{username=$user;password=$pass}|ConvertTo-Json -Compress) -TimeoutSec 10
$h=@{'X-LibreEcho-CSRF'=$csrf;'Authorization'='Bearer '+$login.data.token}

function Val($value,[string]$default){
  if($null -ne $value -and [string]$value -ne ''){ return [string]$value }
  return $default
}
$current=Invoke-RestMethod -UseBasicParsing -Uri "$base/assistant" -Headers $h -TimeoutSec 10
$a=$current.data
$assistant=@{
  enabled=$true
  provider='openai-compatible'
  base_url=("http://{0}:{1}/v1" -f $Endpoint,$Port)
  api_key=$token
  model=(Val $a.model 'olive')
  clock_format=(Val $a.clock_format '24')
  prompt=(Val $a.prompt 'You are Olive, a calm, concise voice assistant.')
}
if($a.home_location){$assistant.home_location=$a.home_location}
if($a.latitude){$assistant.latitude=$a.latitude}
if($a.longitude){$assistant.longitude=$a.longitude}
if($a.weather_provider){$assistant.weather_provider=$a.weather_provider}

$vp=Invoke-RestMethod -UseBasicParsing -Uri "$base/voice-pipeline" -Headers $h -TimeoutSec 10
$v=$vp.data
$pipeline=@{
  mode='custom'
  stt_wyoming_uri=("tcp://{0}:{1}" -f $Endpoint,$AsrPort)
  stt_model=(Val $v.stt.model 'tiny-int8')
  tts_wyoming_uri=(Val $v.tts.wyoming_uri 'tcp://127.0.0.1:10200')
  tts_voice=(Val $v.tts.voice 'en_GB-alan-medium')
}

if($WhatIfOnly){
  "WOULD SET assistant.base_url=$($assistant.base_url)"
  "model=[$($assistant.model)]"
  "WOULD SET stt_wyoming_uri=$($pipeline.stt_wyoming_uri)"
  exit 0
}

$null=Invoke-RestMethod -UseBasicParsing -Uri "$base/assistant" -Method Put -Headers $h `
  -ContentType 'application/json' -Body ($assistant|ConvertTo-Json -Depth 8 -Compress) -TimeoutSec 20
$null=Invoke-RestMethod -UseBasicParsing -Uri "$base/voice-pipeline" -Method Put -Headers $h `
  -ContentType 'application/json' -Body ($pipeline|ConvertTo-Json -Depth 8 -Compress) -TimeoutSec 20

# The device restarts its voice pipeline and drops the session token, so just
# give it time; verify with `voice-pipeline` from the web UI if needed.
Start-Sleep -Seconds 15
"Speaker endpoint set to $Endpoint (HTTP $Port, ASR $AsrPort)."
