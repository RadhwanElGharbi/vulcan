param([int]$WebPort = 3001, [int]$ApiPort = 8000)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskRuntime = Join-Path $env:LOCALAPPDATA 'zeus-runtime'
$taskNodeRoot = Join-Path $env:USERPROFILE '.codex\packages\outreach-runtime\node-v22.23.2-win-x64'
$taskPython = Join-Path $taskRuntime 'python.exe'
$taskNode = Join-Path $taskNodeRoot 'node.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'ZEUS Python runtime is missing. See README.md.' }
if (!(Test-Path -LiteralPath $taskNode)) { $taskNode = (Get-Command node -ErrorAction Stop).Source }
& $taskNode (Join-Path $taskRoot 'gui-v2\frontend\tools\copy-cesium.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare Cesium assets. Run npm install in gui-v2/frontend.' }
$env:PATH = "$taskRuntime;$taskRuntime\Library\bin;$taskRuntime\Scripts;$taskNodeRoot;" + $env:PATH
$env:GDAL_DATA = Join-Path $taskRuntime 'Library\share\gdal'
$env:PROJ_DATA = Join-Path $taskRuntime 'Library\share\proj'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:AGRS_PROJECTS_ROOT = Join-Path $taskRoot 'Projects'
$env:AGRS_DBS_ROOT = Join-Path $taskRoot 'DBs'
$env:AGRS_TILE_CACHE_DIR = Join-Path $taskRoot '.runtime\tiles'
$env:DATASET_JOBS_DB = Join-Path $taskRoot '.runtime\dataset-jobs.sqlite'
$env:ZEUS_AGENT_ENABLED = '0'
$env:AGRS_DBS_MATERIALIZE_MODE = 'copy'
$env:ZEUS_API_URL = "http://127.0.0.1:$ApiPort"
New-Item -ItemType Directory -Path (Join-Path $taskRoot '.runtime') -Force | Out-Null
$taskProcesses = @{}
if (Test-Path -LiteralPath (Join-Path $taskRoot '.runtime\processes.json')) {
    $taskPrevious = Get-Content -LiteralPath (Join-Path $taskRoot '.runtime\processes.json') -Raw | ConvertFrom-Json
    foreach ($taskProperty in $taskPrevious.PSObject.Properties) { $taskProcesses[$taskProperty.Name] = $taskProperty.Value }
}
if (!(Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue)) {
    $taskProcesses.api = (Start-Process -FilePath $taskPython -ArgumentList @('-m','uvicorn','main:app','--host','127.0.0.1','--port',"$ApiPort") -WorkingDirectory (Join-Path $taskRoot 'gui-v2\backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot '.runtime\backend.log') -RedirectStandardError (Join-Path $taskRoot '.runtime\backend-error.log')).Id
}
if (!(Get-NetTCPConnection -State Listen -LocalPort $WebPort -ErrorAction SilentlyContinue)) {
    $taskProcesses.web = (Start-Process -FilePath $taskNode -ArgumentList @('node_modules/next/dist/bin/next','dev','-H','127.0.0.1','-p',"$WebPort") -WorkingDirectory (Join-Path $taskRoot 'gui-v2\frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot '.runtime\frontend.log') -RedirectStandardError (Join-Path $taskRoot '.runtime\frontend-error.log')).Id
}
$taskProcesses | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot '.runtime\processes.json')
$taskReady = $false
for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
    try {
        $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 2
        if ($taskHealth.status -eq 'healthy' -and $taskHealth.version -eq '0.1.0') { $taskReady = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (!$taskReady) { throw 'ZEUS API did not become ready. Check .runtime/backend-error.log and port availability.' }
Write-Output "ZEUS: http://localhost:$WebPort"
