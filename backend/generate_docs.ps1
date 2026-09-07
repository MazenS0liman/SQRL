$ErrorActionPreference = "Stop"

function Write-Title {
    param ([string]$Text)

    Write-Host ""
    Write-Host "===============================" -ForegroundColor Cyan
    Write-Host " $Text" -ForegroundColor Cyan
    Write-Host "===============================" -ForegroundColor Cyan
    Write-Host ""
}

Write-Title "Generating Documentation"

# Ensure script runs from the backend directory
Set-Location $PSScriptRoot

# Activate venv if exists
if (Test-Path ".venv\Scripts\Activate.ps1") {
    Write-Host "Activating virtual environment..." -ForegroundColor Yellow
    . ".venv\Scripts\Activate.ps1"
}
else {
    Write-Host "No .venv found — using system Python" -ForegroundColor DarkYellow
}

if (-not (Get-Command sphinx-build -ErrorAction SilentlyContinue)) {
    throw "sphinx-build was not found. Install the backend documentation dependencies first."
}

# Build docs
if (Test-Path "docs\make.bat") {
    Write-Host "Cleaning docs..." -ForegroundColor Green
    cmd /c "docs\make.bat clean"
    if ($LASTEXITCODE -ne 0) {
        throw "Sphinx documentation cleanup failed. Install the documentation dependencies first."
    }

    Write-Host "Building HTML docs..." -ForegroundColor Green
    cmd /c "docs\make.bat html"
    if ($LASTEXITCODE -ne 0) {
        throw "Sphinx documentation build failed. Install the documentation dependencies first."
    }
}
else {
    throw "docs\make.bat not found. Run sphinx-quickstart first."
}

Write-Host ""
Write-Host "Docs built successfully!" -ForegroundColor Green
Write-Host "HTML output: docs\_build\html\index.html" -ForegroundColor Cyan
$ErrorActionPreference = "Stop"

function Write-Title {
    param ([string]$Text)

    Write-Host ""
    Write-Host "===============================" -ForegroundColor Cyan
    Write-Host " $Text" -ForegroundColor Cyan
    Write-Host "===============================" -ForegroundColor Cyan
    Write-Host ""
}

Write-Title "Generating Documentation"

# Ensure script runs from repo root
Set-Location $PSScriptRoot

# Activate venv if exists
if (Test-Path ".venv\Scripts\Activate.ps1") {
    Write-Host "Activating virtual environment..." -ForegroundColor Yellow
    . ".venv\Scripts\Activate.ps1"
}
else {
    Write-Host "No .venv found — using system Python" -ForegroundColor DarkYellow
}

# Build docs
if (Test-Path "docs\make.bat") {
    Write-Host "Cleaning docs..." -ForegroundColor Green
    cmd /c "docs\make.bat clean"

    Write-Host "Building HTML docs..." -ForegroundColor Green
    cmd /c "docs\make.bat html"
}
else {
    throw "docs\make.bat not found. Run sphinx-quickstart first."
}

Write-Host ""
Write-Host "Docs built successfully!" -ForegroundColor Green
Write-Host "HTML output: docs\build\html\index.html" -ForegroundColor Cyan
