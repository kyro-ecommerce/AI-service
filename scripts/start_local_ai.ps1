$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Local .venv Python is missing.'
}
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8000 is already occupied. Inspect the owner before starting another API.'
}
$logDirectory = Join-Path $projectPath 'artifacts\runtime'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$stdoutPath = Join-Path $logDirectory "local-ai-$stamp.stdout.log"
$stderrPath = Join-Path $logDirectory "local-ai-$stamp.stderr.log"
$aiProcess = Start-Process -FilePath $pythonPath -WorkingDirectory $projectPath `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000') `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
[PSCustomObject]@{
    ProcessId = $aiProcess.Id
    ReadinessUrl = 'http://localhost:8000/api/v1/ai/ready'
    Stdout = $stdoutPath
    Stderr = $stderrPath
}
