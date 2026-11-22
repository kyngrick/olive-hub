#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $PSScriptRoot '.env.example') $envFile
}
$backup = "$envFile.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $envFile $backup

$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([string[]](Get-Content -LiteralPath $envFile))
function Set-EnvLine([string]$Name, [string]$Value) {
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*='
    $found = $false
    $updated = [System.Collections.Generic.List[string]]::new()
    foreach ($line in $lines) {
        if ($line -match $pattern) {
            if (-not $found) {
                $updated.Add("$Name=$Value")
                $found = $true
            }
        } else {
            $updated.Add($line)
        }
    }
    if (-not $found) { $updated.Add("$Name=$Value") }
    $lines.Clear()
    $lines.AddRange($updated)
}

Set-EnvLine 'OLIVE_HOST' '0.0.0.0'
Set-EnvLine 'OLIVE_PORT' '8765'

$existingToken = $null
foreach ($line in $lines) {
    if ($line -match '^\s*OLIVE_LIBREECHO_API_KEY\s*=(.*)$') {
        $existingToken = $Matches[1].Trim().Trim('"').Trim("'")
        break
    }
}
if ([string]::IsNullOrWhiteSpace($existingToken)) {
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    Set-EnvLine 'OLIVE_LIBREECHO_API_KEY' $token
} else {
    $token = $existingToken
}

$content = ($lines -join [Environment]::NewLine) + [Environment]::NewLine
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envFile, $content, $utf8NoBom)

$ruleName = 'Olive Assistant private LAN'
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private -RemoteAddress LocalSubnet | Out-Null

$route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Sort-Object RouteMetric | Select-Object -First 1
$lanIp = $null
if ($route) {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' -and -not $_.SkipAsSource } |
        Select-Object -First 1 -ExpandProperty IPAddress
}
if (-not $lanIp) {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' -and $_.InterfaceOperationalStatus -eq 'Up' } |
        Select-Object -First 1 -ExpandProperty IPAddress
}

try { Set-Clipboard -Value $token } catch {}
$profiles = Get-NetConnectionProfile -ErrorAction SilentlyContinue
$privateProfile = @($profiles | Where-Object { $_.NetworkCategory -eq 'Private' }).Count -gt 0

Write-Host ''
Write-Host 'Olive private-LAN configuration completed.' -ForegroundColor Green
Write-Host "Backup: $backup"
Write-Host "Firewall: TCP 8765, Private profile, LocalSubnet only"
if (-not $privateProfile) {
    Write-Warning 'No active Private network profile was detected. The firewall rule is restricted to Private; set your trusted home Wi-Fi/Ethernet network to Private in Windows Settings.'
}
if ($lanIp) {
    Write-Host "Windows LAN IP: $lanIp" -ForegroundColor Cyan
    Write-Host "LibreEcho Base URL: http://$($lanIp):8765/v1" -ForegroundColor Cyan
} else {
    Write-Warning 'Could not determine the Windows LAN IP. Run ipconfig and use the active Wi-Fi/Ethernet IPv4 address.'
}
Write-Host "LibreEcho model: olive"
Write-Host "LibreEcho API key: $token" -ForegroundColor Yellow
Write-Host 'The API key has also been copied to the clipboard.'
Write-Host 'Restart Olive after this script: press Ctrl+C in its server window, then run .\run-windows.ps1'
Write-Host 'Keep port 8765 off router port-forwarding and public networks.'
