# Start the Olive Hub dashboard on port 8767 (DASH_PORT in .env overrides).
$ErrorActionPreference='Stop'
$root='C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2'
$py=Join-Path $root '.venv\Scripts\python.exe'
$log=Join-Path $root 'dashboard.log'
$err=Join-Path $root 'dashboard.err'
function Test-Listening([int]$p){
  $rows=& netstat.exe -ano -p TCP 2>$null | Select-String -Pattern 'LISTENING\s+\d+\s*$'
  foreach($row in $rows){$parts=($row.Line -split '\s+')|Where-Object{$_};if($parts.Count -ge 5 -and $parts[1] -match ":$p`$"){return $true}}
  return $false
}
if(Test-Listening 8767){'Dashboard already listening on 8767';exit 0}
$scriptPath=Join-Path $root 'dashboard.py'
$p=Start-Process -FilePath $py -ArgumentList ('"' + $scriptPath + '"') `
  -WorkingDirectory $root -RedirectStandardOutput $log -RedirectStandardError $err `
  -WindowStyle Hidden -PassThru
"started dashboard pid $($p.Id)"
$deadline=(Get-Date).AddSeconds(20)
while((Get-Date) -lt $deadline){if(Test-Listening 8767){'Dashboard listening on 8767';exit 0};Start-Sleep -Seconds 1}
'TIMEOUT waiting for 8767; check dashboard.err';exit 1

