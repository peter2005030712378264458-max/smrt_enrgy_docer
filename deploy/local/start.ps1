$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
if (!(Test-Path deploy/local/app.env)) { throw 'Run python deploy/local/configure.py first' }
docker compose -p smart-energy-workstation -f docker-compose.local.yml up -d --build ssh
if ($LASTEXITCODE -ne 0) { throw 'SSH startup failed' }
docker compose -p smart-energy-workstation -f docker-compose.local.yml build backend analytical adapter frontend
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
docker compose -p smart-energy-workstation -f docker-compose.local.yml run --rm --entrypoint python adapter -m adapter.local_setup
if ($LASTEXITCODE -ne 0) { throw 'Control schema initialization failed' }
docker compose -p smart-energy-workstation -f docker-compose.local.yml up -d
if ($LASTEXITCODE -ne 0) { throw 'Application startup failed' }
Start-Process 'https://localhost:8443/'
