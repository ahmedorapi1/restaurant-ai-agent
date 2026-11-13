[CmdletBinding()]
param([switch]$DatabaseOnly)

$ErrorActionPreference = 'Stop'

function Test-SofraApi {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
        if ($health.status -ne 'ok') { return $false }
        $spec = Invoke-RestMethod 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 2
        return $spec.info.title -eq 'Restaurant Chat API'
    } catch {
        return $false
    }
}

function Test-SofraFrontend {
    try {
        $page = Invoke-WebRequest 'http://127.0.0.1:5173/' -TimeoutSec 2 -UseBasicParsing
        $module = Invoke-WebRequest 'http://127.0.0.1:5173/src/api.ts' -TimeoutSec 2 -UseBasicParsing
        return $page.Content.Contains('/src/main.tsx') -and $module.Content.Contains('sofra.session:')
    } catch {
        return $false
    }
}

function Start-SofraService {
    param($Name, $Executable, $Arguments, $Directory, [scriptblock]$Ready)

    if (& $Ready) {
        Write-Host "$Name already running."
        return
    }
    $logFile = Join-Path $logDirectory "$Name.log"
    $errorFile = Join-Path $logDirectory "$Name.error.log"
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $Directory `
        -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errorFile -PassThru
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline -and -not $process.HasExited) {
        if (& $Ready) {
            Write-Host "$Name ready."
            return
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $process.HasExited) { Stop-Process -Id $process.Id }
    throw "$Name did not start. Check $errorFile and whether its port is already in use."
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $python = @('.venv\Scripts\python.exe', '.venv312\Scripts\python.exe') |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $python) { throw 'Set up the Python environment first. See README.md.' }
    $python = (Resolve-Path -LiteralPath $python).Path
    if ($env:SOFRA_DB_PORT -or $env:SOFRA_DB_PASSWORD) {
        throw 'Put the Compose database settings in .env and remove any SOFRA_DB_PORT/SOFRA_DB_PASSWORD shell overrides.'
    }

    docker compose up -d --wait --wait-timeout 60 postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL did not start. Check Docker Desktop and .env.' }
    & $python -m app.database.bootstrap_local
    if ($LASTEXITCODE -ne 0) { throw 'Database setup failed.' }
    if ($DatabaseOnly) { return }

    $frontend = Join-Path $PSScriptRoot 'frontend'
    if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules/vite/bin/vite.js'))) {
        throw 'Install frontend dependencies with npm.cmd ci from frontend first.'
    }
    $node = (Get-Command node.exe).Source
    $logDirectory = Join-Path $PSScriptRoot '.local'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

    Start-SofraService 'backend' $python @('-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1', '--port', '8000') $PSScriptRoot `
        ${function:Test-SofraApi}
    Start-SofraService 'frontend' $node @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173', '--strictPort') $frontend `
        ${function:Test-SofraFrontend}
    Write-Host 'Sofra is available at http://127.0.0.1:5173/'
} finally {
    Pop-Location
}
