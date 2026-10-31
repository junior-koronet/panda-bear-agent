# Panda Bear — health check (Programador de tareas, cada 5 minutos).
# Covers what the watchdog (run_agent_loop.bat) can't: a python process that is
# alive but hung, or the watchdog itself being gone. If /health doesn't answer
# 3 times in a row, kills the hung agent (the watchdog restarts it) or, with no
# watchdog running, launches start_agent_hidden.vbs. Logs to healthcheck.log
# (panda_bear_startup.log is held open by the running agent).

$ErrorActionPreference = 'SilentlyContinue'
$root = 'C:\PandaBearAgent'
$log = Join-Path $root 'healthcheck.log'

function Write-Log($text) {
    Add-Content -Path $log -Value "[healthcheck] $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') $text" -Encoding UTF8
}

for ($i = 0; $i -lt 3; $i++) {
    try {
        $r = Invoke-WebRequest 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { exit 0 }
    } catch {}
    Start-Sleep -Seconds 15
}

$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'main\.py' -and $_.Name -match 'python' }
$watchdog = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_agent_loop' }

if ($procs) {
    Write-Log "/health no responde: reinicio el agente colgado (pid $($procs.ProcessId -join ','))"
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}
if (-not $watchdog) {
    Write-Log "watchdog no estaba corriendo: lanzo start_agent_hidden.vbs"
    Start-Process -FilePath "$env:WINDIR\System32\wscript.exe" -ArgumentList "`"$root\start_agent_hidden.vbs`"" -WorkingDirectory $root
}
