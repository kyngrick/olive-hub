param(
 [string]$LibreEchoUrl = "http://192.168.1.228:8080",
 [string]$OliveBaseUrl = "http://192.168.1.86:8765/v1",
 [string]$Model = "olive",
 [switch]$TestAnnouncement
)
$ErrorActionPreference = "Stop"
$LibreEchoUrl = $LibreEchoUrl.TrimEnd("/")
$OliveBaseUrl = $OliveBaseUrl.TrimEnd("/")
$ApiRoot = "$LibreEchoUrl/api/v1"

function Read-SecretText([string]$Prompt) {
 $s = Read-Host -Prompt $Prompt -AsSecureString
 $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
 try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) }
 finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p) }
}
function Call-LeApi([string]$Method,[string]$Path,[object]$Body=$null,[bool]$Auth=$true) {
 $h = @{ "X-LibreEcho-CSRF" = $Csrf }
 if ($Auth) { $h["Authorization"] = "Bearer $Session" }
 $a = @{ Uri="$ApiRoot$Path"; Method=$Method; Headers=$h; TimeoutSec=20 }
 if ($null -ne $Body) { $a.ContentType="application/json"; $a.Body=($Body | ConvertTo-Json -Depth 8 -Compress) }
 Invoke-RestMethod @a
}

Write-Host "OLIVE + LIBREECHO DIRECT VOICE" -ForegroundColor Cyan
Write-Host "This configures LibreEcho Local mode; Home Assistant is not the voice assistant."

$healthUrl = ($OliveBaseUrl -replace "/v1$","") + "/api/health"
$health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 8
if (-not $health.ok -or -not $health.voice_bridge_configured) {
 throw "Olive is reachable but its dedicated voice bridge is not configured. Set OLIVE_LIBREECHO_API_KEY in Olive's existing .env and restart Olive. No Echo settings changed."
}
Write-Host "[OK] Olive is reachable and voice authentication is configured." -ForegroundColor Green

$config = Invoke-RestMethod -Uri "$ApiRoot/config" -TimeoutSec 10
$Csrf = $config.data.csrf_token
if ([string]::IsNullOrWhiteSpace($Csrf)) { throw "LibreEcho did not return its CSRF token." }

$user = Read-Host "LibreEcho administrator username"
$pass = Read-SecretText "LibreEcho administrator password"
try {
 $login = Invoke-RestMethod -Uri "$ApiRoot/auth/login" -Method POST -Headers @{ "X-LibreEcho-CSRF"=$Csrf } -ContentType "application/json" -Body (@{username=$user;password=$pass}|ConvertTo-Json -Compress) -TimeoutSec 15
} finally { $pass=$null }
$Session = $login.data.token
if ([string]::IsNullOrWhiteSpace($Session)) { throw "LibreEcho login failed; no assistant settings were changed." }
Write-Host "[OK] LibreEcho authenticated." -ForegroundColor Green

$key = Read-SecretText "Olive OLIVE_LIBREECHO_API_KEY (hidden input)"
try {
 $modelCheck = Invoke-RestMethod -Uri "$OliveBaseUrl/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 8
 if (-not ($modelCheck.data | Where-Object { $_.id -eq $Model })) { throw "Model '$Model' was not listed by Olive." }
} catch {
 $key = $null
 throw "Olive's authenticated /v1/models check failed. Verify the key against OLIVE_LIBREECHO_API_KEY in the running Olive .env. No LibreEcho assistant settings were changed."
}
Write-Host "[OK] Olive voice token accepted by /v1/models." -ForegroundColor Green

$assistant = @{
 enabled=$true
 provider="openai-compatible"
 base_url=$OliveBaseUrl
 api_key=$key
 model=$Model
 clock_format="24"
 prompt="You are Olive, the user's personal voice assistant. Reply in concise, natural spoken English. No Markdown, URLs, citations, emoji, or stage directions. Never claim an action succeeded unless a connected tool confirms it."
}
try { $null = Call-LeApi "PUT" "/assistant" $assistant $true }
finally { $key=$null; $assistant.api_key=$null }
Write-Host "[OK] Assistant provider now points to Olive." -ForegroundColor Green

# Local mode means LibreEcho itself owns wake word, STT and TTS. It disables
# the Home Assistant ESPHome voice owner without removing other integrations.
$null = Call-LeApi "PUT" "/voice-pipeline" @{mode="local"} $true
Write-Host "[OK] Requested LibreEcho Local voice pipeline." -ForegroundColor Green

$until = (Get-Date).AddSeconds(75)
do {
 Start-Sleep -Seconds 2
 $status = Call-LeApi "GET" "/voice-pipeline" $null $true
 $state = $status.data.restart.state
 if ($state -eq "ready") { break }
 if ($state -eq "failed") { throw "Local voice pipeline failed: $($status.data.restart.error)" }
} while ((Get-Date) -lt $until)
if ($state -ne "ready") { throw "Restart is still pending after 75 seconds. Check LibreEcho Logs; do not resubmit." }
Write-Host "[OK] Local voice pipeline is ready." -ForegroundColor Green

if ($TestAnnouncement) {
 $null = Call-LeApi "POST" "/audio/announce" @{text="Olive is connected. Your local voice assistant is ready."} $true
 Write-Host "[OK] Local TTS announcement sent to Echo." -ForegroundColor Green
}
Write-Host ""
Write-Host "DONE: Echo wake word, microphone, STT and TTS stay local to LibreEcho." -ForegroundColor Green
Write-Host "Olive on Windows supplies the conversational response. Home Assistant is not in this voice path."
Write-Host "No public port forwarding was configured."
