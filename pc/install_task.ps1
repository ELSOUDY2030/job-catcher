# Registers "JobCatcher PC": runs pc_runner.py every 30 minutes while you are logged in
# (Edge must be open for applying). Runs on battery too; never two at once. No console window.
# Remove it with:  Unregister-ScheduledTask -TaskName "JobCatcher PC" -Confirm:$false
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\pythonw.exe"
$runner = Join-Path $PSScriptRoot "pc_runner.py"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$runner`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable
Register-ScheduledTask -TaskName "JobCatcher PC" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Job Catcher: scan Wuzzuf/Bayt + apply via Antigravity, every 30 min" -Force | Out-Null
Write-Host "Registered 'JobCatcher PC' (every 30 minutes). Log: $root\receipts\runner.log"
