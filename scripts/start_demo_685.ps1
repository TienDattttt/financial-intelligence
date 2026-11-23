<#
.SYNOPSIS
Launch FinIntel 685-issuer defense demo against data/universe_600_800 (read-only).

.DESCRIPTION
Pins FININTEL_DATA_ROOT=data/universe_600_800 so FastAPI never boots against the
empty default data/metadata/state.sqlite (P1-5). Performs a read-only check on
state.sqlite before starting Uvicorn (and optionally Next.js frontend). Never
mutates data/ or triggers external EOD price collection.
#>
[CmdletBinding()]
param(
    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DataRoot = Join-Path $RepoRoot "data\universe_600_800"
$StateSqlite = Join-Path $DataRoot "metadata\state.sqlite"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment Python not found at '$PythonExe'. Run 'uv sync --locked --group ml --group platform --dev' first."
}
if (-not (Test-Path $StateSqlite)) {
    throw "685-issuer state.sqlite not found at '$StateSqlite'."
}

$env:FININTEL_DATA_ROOT = $DataRoot
if (-not $env:FININTEL_STORAGE_BACKEND) {
    $env:FININTEL_STORAGE_BACKEND = "ducklake"
}
if (-not $env:FININTEL_DUCKLAKE_CATALOG_DRIVER) {
    $env:FININTEL_DUCKLAKE_CATALOG_DRIVER = "sqlite"
}
$env:PYTHONPATH = "$RepoRoot;$(Join-Path $RepoRoot 'src')"

$activeSnap = & $PythonExe -c @"
import sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1])
with sqlite3.connect(f'{p.as_uri()}?mode=ro', uri=True, timeout=5) as conn:
    row = conn.execute('SELECT snapshot_id FROM active_snapshot WHERE singleton = 1').fetchone()
if row is None or not row[0]:
    sys.exit(2)
print(row[0])
"@ $StateSqlite

if ($LASTEXITCODE -ne 0) {
    throw "active_snapshot is empty in '$StateSqlite'. Refusing to start empty demo."
}

Write-Host "[FinIntel Demo] FININTEL_DATA_ROOT=$env:FININTEL_DATA_ROOT"
Write-Host "[FinIntel Demo] Active snapshot=$activeSnap"
Write-Host "[FinIntel Demo] Starting FastAPI at http://${ApiHost}:${ApiPort}"

if ($BackendOnly) {
    Push-Location $RepoRoot
    try {
        & $PythonExe -m uvicorn finintel.serving.api:app --host $ApiHost --port $ApiPort
    } finally {
        Pop-Location
    }
    exit $LASTEXITCODE
}

$env:NEXT_PUBLIC_API_BASE_URL = "http://${ApiHost}:${ApiPort}"
$backendProc = Start-Process -FilePath $PythonExe `
    -ArgumentList @("-m", "uvicorn", "finintel.serving.api:app", "--host", $ApiHost, "--port", "$ApiPort") `
    -WorkingDirectory $RepoRoot `
    -PassThru

try {
    Write-Host "[FinIntel Demo] Starting Next.js frontend at http://127.0.0.1:${FrontendPort}"
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        if (Get-Command fnm -ErrorAction SilentlyContinue) {
            fnm exec --using=default -- npm.cmd run dev -- --port $FrontendPort
        } else {
            npm run dev -- --port $FrontendPort
        }
    } finally {
        Pop-Location
    }
} finally {
    if ($backendProc -and -not $backendProc.HasExited) {
        Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue
    }
}
