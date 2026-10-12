<#
Run ProofPlate locally.

    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1            # API on :8000 serving the built web app
    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Seed      # also attach dev user "manager" to site "local-pilot"

Local mode uses SQLite (proofplate-local.db) and a development sign-in that
the server refuses to enable anywhere but PROOFPLATE_ENV=local.
For live reload of the web app, run `npm run dev` in apps\web as well and open :5173.
#>
param([switch]$Seed, [int]$Port = 8000)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PROOFPLATE_ENV = "local"
$env:PROOFPLATE_AUTH = "dev"
$env:PROOFPLATE_STORE = "sqlite"
$env:PROOFPLATE_DB = Join-Path $root "proofplate-local.db"
$env:PROOFPLATE_STATIC = Join-Path $root "apps\web\dist"
$env:PYTHONPATH = Join-Path $root "services\api"
if ($Seed) {
  python -c "from proofplate.main import pilot_repo; r = pilot_repo(); r.put_membership('dev:manager', 'local-pilot', 'manager'); r.put_membership('dev:operator', 'local-pilot', 'operator'); print('dev users: manager, operator -> site local-pilot')"
}
python -m uvicorn proofplate.main:app --host 127.0.0.1 --port $Port
