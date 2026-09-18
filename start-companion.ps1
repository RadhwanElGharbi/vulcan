param([string]$RuntimeDirectory = '', [int]$ApiPort = 8000, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskState = Join-Path $env:LOCALAPPDATA 'Vulcan'
New-Item -ItemType Directory -Path $taskState -Force | Out-Null
$taskUrl = "http://127.0.0.1:$ApiPort/api/health"
try {
    $taskExisting = Invoke-RestMethod $taskUrl -TimeoutSec 2
    if ($taskExisting.application -eq 'VULCAN' -and $taskExisting.companion_protocol -eq 1) {
        Write-Output 'VULCAN companion is already running.'
        if (!$NoBrowser) { Start-Process 'https://vulcan.colony.tech' }
        exit 0
    }
} catch { }
if (!$RuntimeDirectory) { $RuntimeDirectory = Join-Path $taskState 'runtime' }
$taskPython = Join-Path $RuntimeDirectory 'python.exe'
$taskReadyMarker = Join-Path $RuntimeDirectory '.vulcan-ready'
if (!(Test-Path -LiteralPath $taskReadyMarker)) {
    $taskMamba = Join-Path $taskState 'micromamba.exe'
    $taskMambaHash = '90085767d811e08a0fe240f7cb7a713915e1e5f24e3b0acfad9b37daa64c98fe'
    if (!(Test-Path -LiteralPath $taskMamba)) {
        Write-Output 'Downloading the VULCAN runtime installer...'
        Invoke-WebRequest 'https://github.com/mamba-org/micromamba-releases/releases/download/2.3.3-0/micromamba-win-64.exe' -OutFile $taskMamba -UseBasicParsing
    }
    if ((Get-FileHash -LiteralPath $taskMamba -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskMambaHash) { throw 'Runtime installer integrity check failed.' }
    $taskCache = Join-Path $taskState 'runtime-cache'
    Write-Output 'Installing the pinned scientific runtime. This can take several minutes...'
    Push-Location $env:TEMP
    try {
        & $taskMamba --no-rc -r $taskCache create -y -p $RuntimeDirectory -f (Join-Path $taskRoot 'docs\datasets\reference-runtime\conda-win-64.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Runtime installation failed. Run start-companion.cmd to retry.' }
    } finally { Pop-Location }
    & $taskPython (Join-Path $taskRoot 'qa\verify_runtime_archives.py') --prefix $RuntimeDirectory --cache (Join-Path $taskCache 'pkgs') --record
    if ($LASTEXITCODE -ne 0) { throw 'Runtime archive verification failed.' }
    & $taskPython -m pip install --no-deps --no-build-isolation --require-hashes -r (Join-Path $taskRoot 'docs\datasets\reference-runtime\pip-win-64.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed. Run start-companion.cmd to retry.' }
    '1' | Set-Content -LiteralPath $taskReadyMarker
}
$env:PATH = "$RuntimeDirectory;$RuntimeDirectory\Library\bin;$RuntimeDirectory\Scripts;" + $env:PATH
$env:GDAL_DATA = Join-Path $RuntimeDirectory 'Library\share\gdal'
$env:PROJ_DATA = Join-Path $RuntimeDirectory 'Library\share\proj'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:AGRS_PROJECTS_ROOT = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Vulcan\Projects'
$env:AGRS_DBS_ROOT = Join-Path $taskState 'datasets'
$env:AGRS_TILE_CACHE_DIR = Join-Path $taskState 'tiles'
$env:ZEUS_RESEARCH_STORE = Join-Path $taskState 'acquisition'
$env:ZEUS_WORKSPACE_SETTINGS = Join-Path $taskState 'workspace.json'
$env:AGRS_DBS_MATERIALIZE_MODE = 'copy'
$env:ZEUS_AGENT_ENABLED = '0'
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-m','uvicorn','main:app','--host','127.0.0.1','--port',"$ApiPort") -WorkingDirectory (Join-Path $taskRoot 'apps\api') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskState 'companion.log') -RedirectStandardError (Join-Path $taskState 'companion-error.log')
$taskReady = $false
for ($taskAttempt = 0; $taskAttempt -lt 40; $taskAttempt++) {
    if ($taskProcess.HasExited) { break }
    try { $taskHealth = Invoke-RestMethod $taskUrl -TimeoutSec 2; if ($taskHealth.application -eq 'VULCAN') { $taskReady = $true; break } } catch { }
    Start-Sleep -Milliseconds 500
}
if (!$taskReady) { throw "VULCAN did not start. See $taskState\companion-error.log" }
Write-Output 'VULCAN companion is running in the background. You can close this window.'
if (!$NoBrowser) { Start-Process 'https://vulcan.colony.tech' }
