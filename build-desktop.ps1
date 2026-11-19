param(
    [Parameter(Mandatory = $true)] [string] $PythonExe,
    [string] $OutputRoot
)
$ErrorActionPreference = 'Stop'
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
if (-not $OutputRoot) { $OutputRoot = Join-Path $PSScriptRoot 'desktop-builds' }
$runName = 'v1.21-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$runRoot = Join-Path $OutputRoot $runName
if (Test-Path -LiteralPath $runRoot) { throw 'Build output already exists.' }
$null = New-Item -ItemType Directory -Path $runRoot
$frontendDir = Join-Path $runRoot 'frontend'
$priorFrontend = $env:YUKETANG_DESKTOP_FRONTEND
$priorVersion = $env:VITE_APP_VERSION
$priorDesktop = $env:VITE_DESKTOP_BUILD
try {
    $env:YUKETANG_DESKTOP_FRONTEND = $frontendDir
    $env:VITE_APP_VERSION = 'V1.21'
    $env:VITE_DESKTOP_BUILD = '1'
    Push-Location (Join-Path $PSScriptRoot 'frontend')
    try {
        & npm.cmd run build -- --outDir $frontendDir
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
    & $PythonExe -m PyInstaller --distpath (Join-Path $runRoot 'dist') --workpath (Join-Path $runRoot 'work') (Join-Path $PSScriptRoot 'YuketangDesktop.spec')
    if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed.' }
    Write-Output "Desktop executable: $(Join-Path $runRoot 'dist\YuketangEvan\YuketangEvan.exe')"
} finally {
    $env:YUKETANG_DESKTOP_FRONTEND = $priorFrontend
    $env:VITE_APP_VERSION = $priorVersion
    $env:VITE_DESKTOP_BUILD = $priorDesktop
}
