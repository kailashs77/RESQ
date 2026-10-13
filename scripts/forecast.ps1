<#
.SYNOPSIS
Build, train and serve the ResQ traffic-forecasting stack.

.DESCRIPTION
Three checkpoints are produced by this pipeline.

  METR-LA and PEMS-BAY  public benchmarks that validate the model class against
                        the required persistence and historical-average baselines.
  CHENNAI-SIM           the corridor checkpoint the decision brain actually uses,
                        trained on repeatable SUMO runs of the study area.

Training uses .venv-forecast (CUDA build of PyTorch); the API serves inference from
.venv on the CPU.
#>
param(
    [switch]$Benchmarks,
    [switch]$Corridor,
    [switch]$All,
    [int]$Days = 21,
    [int]$Segments = 120,
    [int]$Workers = 6,
    [int]$Seed = 42,
    [int]$Epochs = 100
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot

$app = Join-Path $projectRoot '.venv\Scripts\python.exe'
$trainer = Join-Path $projectRoot '.venv-forecast\Scripts\python.exe'
foreach ($interpreter in @($app, $trainer)) {
    if (-not (Test-Path -LiteralPath $interpreter)) {
        throw "Missing interpreter $interpreter. See docs/TRAFFIC_FORECASTING_RUN.md."
    }
}
if (-not ($Benchmarks -or $Corridor -or $All)) { $All = $true }

function Invoke-Step([string]$Label, [string]$Exe, [string[]]$Arguments) {
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

if ($Benchmarks -or $All) {
    foreach ($dataset in @('METR-LA', 'PEMS-BAY')) {
        Invoke-Step "prepare $dataset" $trainer @('-m', 'forecast.training.cli', 'prepare', $dataset)
        Invoke-Step "baseline $dataset" $trainer @('-m', 'forecast.training.cli', 'baseline', $dataset)
        Invoke-Step "train $dataset" $trainer @(
            '-m', 'forecast.training.cli', 'train', $dataset,
            '--seed', $Seed, '--batch', '64', '--epochs', $Epochs, '--patience', '15'
        )
    }
}

if ($Corridor -or $All) {
    Invoke-Step 'build simulated corridor histories' $app @(
        '-m', 'sim.chennai.forecast_dataset',
        '--days', $Days, '--segments', $Segments, '--workers', $Workers, '--seed', $Seed
    )
    Invoke-Step 'baseline CHENNAI-SIM' $trainer @(
        '-m', 'forecast.training.cli', 'baseline', 'CHENNAI-SIM'
    )
    Invoke-Step 'train CHENNAI-SIM' $trainer @(
        '-m', 'forecast.training.cli', 'train', 'CHENNAI-SIM',
        '--seed', $Seed, '--batch', '32', '--epochs', $Epochs, '--patience', '15'
    )
}

Write-Host "`nCheckpoints in artifacts\forecast. Start the demo with:" -ForegroundColor Green
Write-Host '  .venv\Scripts\python.exe -m uvicorn api.main:app --reload'
