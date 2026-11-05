$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
docker compose -p smart-energy-workstation -f docker-compose.local.yml stop
