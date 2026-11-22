# Start (or restart) olive_hub.py from the project venv.
#   .\start-hub.ps1            # foreground-less restart, logs to olive_hub.log/.err
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$venv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venv)) { throw "venv not found: $venv" }

$ports = @(8765, 10300)
$pids = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $ports } |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($id in $pids) {
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like 'python*') {
        Write-Host "stopping $($proc.ProcessName) pid $($proc.Id)"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

$out = Join-Path $PSScriptRoot 'olive_hub.log'
$err = Join-Path $PSScriptRoot 'olive_hub.err'
if (Test-Path $err) { Move-Item $err "$err.prev" -Force }
if (Test-Path $out) { Move-Item $out "$out.prev" -Force }

$p = Start-Process -FilePath $venv -ArgumentList "`"$PSScriptRoot\olive_hub.py`"" `
    -WorkingDirectory $PSScriptRoot -RedirectStandardOutput $out -RedirectStandardError $err `
    -PassThru -WindowStyle Hidden
Write-Host "started pid $($p.Id)"
Start-Sleep -Seconds 8
Get-Content $err -Tail 8
