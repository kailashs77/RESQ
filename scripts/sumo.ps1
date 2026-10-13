param(
    [switch]$Build,
    [switch]$Landscape,
    [switch]$Headless,
    [int]$DelayMs = 50
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project .venv not found. Create it and install the SUMO extra first.'
}

if ($Build -or -not (Test-Path -LiteralPath 'sim\chennai\generated\thousand_lights.net.xml')) {
    & $python 'sim\chennai\build_network.py'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} elseif ($Landscape) {
    # Redraw the background map and route markers without re-downloading OSM.
    & $python 'sim\chennai\build_network.py' --landscape-only
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python 'sim\chennai\build_ambulance_route.py'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Headless) {
    & $python -m sim.chennai.run_sumo
} else {
    & $python -m sim.chennai.run_sumo --gui --delay-ms $DelayMs
}
exit $LASTEXITCODE
