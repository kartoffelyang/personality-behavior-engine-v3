$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appPath = Join-Path $projectRoot "web\app.py"
$pidPath = Join-Path $projectRoot ".local-server.pid"
$stdoutPath = Join-Path $projectRoot "web\local-server.stdout.log"
$stderrPath = Join-Path $projectRoot "web\local-server.stderr.log"
$siteUrl = "http://127.0.0.1:10086/"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment is missing. Recreate .venv and install requirements.txt."
}

if (Test-Path -LiteralPath $pidPath) {
    $savedPid = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue
    if ($savedPid -and (Get-Process -Id $savedPid -ErrorAction SilentlyContinue)) {
        Start-Process $siteUrl
        exit 0
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$listener = Get-NetTCPConnection -LocalPort 10086 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port 10086 is already in use by process $($listener.OwningProcess)."
}

$server = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @($appPath, "--host", "127.0.0.1", "--port", "10086") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $server.Id -Encoding ascii
Start-Sleep -Seconds 2

if ($server.HasExited) {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    throw "The local server exited during startup. Check web\local-server.stderr.log."
}

Start-Process $siteUrl

