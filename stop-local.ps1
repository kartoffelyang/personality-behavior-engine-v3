$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidPath = Join-Path $projectRoot ".local-server.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "No recorded local server process was found."
    exit 0
}

$savedPid = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue
if ($savedPid) {
    $server = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($server) {
        Stop-Process -Id $savedPid
        $server.WaitForExit(5000)
    }
}

Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Write-Host "The local server has stopped."

