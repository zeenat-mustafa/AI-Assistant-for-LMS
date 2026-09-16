# AI Assistant for LMS — Unified Startup Script
# Starts both backend (uvicorn) and frontend (Next.js dev server) in one command

Write-Host "Starting AI Assistant for LMS..." -ForegroundColor Cyan
Write-Host ""

# Find Python executable
$pythonExe = "C:\ProgramData\miniconda3\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Host "Error: Python not found at $pythonExe" -ForegroundColor Red
    Write-Host "Update the `$pythonExe path in start-all.ps1 to match your system" -ForegroundColor Yellow
    exit 1
}

# Get absolute path to repo root
$repoRoot = $PSScriptRoot
if (-not $repoRoot) {
    $repoRoot = Get-Location
}

# Verify backend directory exists
$backendPath = Join-Path $repoRoot "backend"
if (-not (Test-Path $backendPath)) {
    Write-Host "Error: backend/ directory not found at $backendPath" -ForegroundColor Red
    Write-Host "Run this script from the repository root." -ForegroundColor Yellow
    exit 1
}

# Verify frontend directory exists
$frontendPath = Join-Path $repoRoot "frontend"
if (-not (Test-Path $frontendPath)) {
    Write-Host "Error: frontend/ directory not found at $frontendPath" -ForegroundColor Red
    Write-Host "Run this script from the repository root." -ForegroundColor Yellow
    exit 1
}

# Start backend (no --reload for demo stability)
Write-Host "[backend] Starting FastAPI server on port 8000..." -ForegroundColor Green
$backendJob = Start-Job -ScriptBlock {
    param($pythonPath, $workDir)
    Set-Location $workDir
    & $pythonPath -m uvicorn app.main:app --port 8000 2>&1 | ForEach-Object { "[backend] $_" }
} -ArgumentList $pythonExe, $backendPath

# Start frontend
Write-Host "[frontend] Starting Next.js dev server on port 3000..." -ForegroundColor Blue
$frontendJob = Start-Job -ScriptBlock {
    param($workDir)
    Set-Location $workDir
    npm run dev 2>&1 | ForEach-Object { "[frontend] $_" }
} -ArgumentList $frontendPath

Write-Host ""
Write-Host "Both services starting..." -ForegroundColor Cyan
Write-Host "  Backend:  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "  Frontend: http://localhost:3000" -ForegroundColor Blue
Write-Host ""
Write-Host "Press Ctrl+C to stop both services" -ForegroundColor Yellow
Write-Host ""

# Stream output from both jobs
try {
    while ($true) {
        # Receive and display backend output
        if ($backendJob.State -eq "Running") {
            Receive-Job -Job $backendJob | Write-Host
        }
        
        # Receive and display frontend output
        if ($frontendJob.State -eq "Running") {
            Receive-Job -Job $frontendJob | Write-Host
        }
        
        # Check if both jobs have stopped
        if ($backendJob.State -ne "Running" -and $frontendJob.State -ne "Running") {
            Write-Host ""
            Write-Host "Both services have stopped." -ForegroundColor Yellow
            break
        }
        
        Start-Sleep -Milliseconds 100
    }
}
finally {
    # Cleanup on Ctrl+C or script exit
    Write-Host ""
    Write-Host "Stopping services..." -ForegroundColor Yellow
    
    # Stop both jobs
    if ($backendJob.State -eq "Running") {
        Stop-Job -Job $backendJob
        Write-Host "[backend] Stopped" -ForegroundColor Green
    }
    if ($frontendJob.State -eq "Running") {
        Stop-Job -Job $frontendJob
        Write-Host "[frontend] Stopped" -ForegroundColor Blue
    }
    
    # Clean up job objects
    Remove-Job -Job $backendJob -Force -ErrorAction SilentlyContinue
    Remove-Job -Job $frontendJob -Force -ErrorAction SilentlyContinue
    
    Write-Host "All services stopped." -ForegroundColor Cyan
}
