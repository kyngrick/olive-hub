# Register the "Olive Hub" scheduled task (needs one elevation / UAC).
#
# Result: runs as the current user (S4U, no stored password, not SYSTEM),
# starts at boot, and a 5-minute watchdog restarts the hub if it stops.
# The hub keeps running across logoff/logon because the task does not depend
# on an interactive session.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$log = Join-Path $root 'task-register.log'

function Write-Result([string]$message) {
    Add-Content -Path $log -Value ('{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $message) -Encoding utf8
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principalCheck = [Security.Principal.WindowsPrincipal]$identity
    if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Result 'ERROR: registration must run elevated'
        exit 1
    }

    $watchdog = Join-Path $root 'ensure-hub.ps1'
    if (-not (Test-Path $watchdog)) { throw "watchdog missing: $watchdog" }

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $watchdog) `
        -WorkingDirectory $root

    $startup = New-ScheduledTaskTrigger -AtStartup
    $startup.Delay = 'PT30S'

    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)

    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name `
        -LogonType S4U -RunLevel Limited

    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

    Register-ScheduledTask -TaskName 'Olive Hub' `
        -Description 'Starts the Olive voice hub at boot and restarts it within 5 minutes if it stops.' `
        -Action $action -Trigger @($startup, $repeat) `
        -Principal $principal -Settings $settings -Force | Out-Null

    $task = Get-ScheduledTask -TaskName 'Olive Hub'
    Write-Result ('OK: {0} | state={1} | user={2} | logon={3}' -f `
        $task.TaskName, $task.State, $task.Principal.UserId, $task.Principal.LogonType)
    exit 0
} catch {
    Write-Result ('ERROR: ' + $_.Exception.Message)
    exit 1
}
