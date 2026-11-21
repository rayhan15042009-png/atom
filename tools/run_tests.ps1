<#
.SYNOPSIS
    Run the Echo test suite on Windows without requiring bash.
.DESCRIPTION
    Runs all three test scripts (test_3d_system.py, test_parse.py, integration_test.py)
    sequentially using the project's virtual environment Python.
.EXAMPLE
    .\tools\run_tests.ps1
    .\tools\run_tests.ps1 -SkipSetup
#>

param(
    [switch]$SkipSetup = $false
)

$ErrorActionPreference = "Continue"
$ROOT = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$VENV_PYTHON = Join-Path $ROOT ".venv" "Scripts" "python.exe"

# Colors
$GREEN = "Green"
$YELLOW = "Yellow"
$RED = "Red"
$CYAN = "Cyan"

Write-Host ("=" * 65)
Write-Host "  Atom Test Suite Runner" -ForegroundColor $CYAN
Write-Host "  Root: $ROOT" -ForegroundColor $CYAN
Write-Host ("=" * 65)
Write-Host ""

# Check venv
if (-not (Test-Path $VENV_PYTHON)) {
    Write-Host "[!] Virtual environment not found at:" -ForegroundColor $RED
    Write-Host "    $VENV_PYTHON"
    Write-Host "    Run the setup or create a venv first."
    exit 1
}

Write-Host "[*] Using Python: $VENV_PYTHON" -ForegroundColor $CYAN
Write-Host ""

$tests = @(
    @{ Name = "3D System Tests"; Path = "tools\test_3d_system.py" },
    @{ Name = "Action Parse Tests"; Path = "tools\test_parse.py" },
    @{ Name = "Integration Tests"; Path = "tools\integration_test.py" }
)

$passed = 0
$failed = 0
$results = @()

foreach ($test in $tests) {
    $testPath = Join-Path $ROOT $test.Path
    if (-not (Test-Path $testPath)) {
        Write-Host "[!] Test file not found: $($test.Path)" -ForegroundColor $YELLOW
        continue
    }

    Write-Host ("─" * 44) -ForegroundColor $CYAN
    Write-Host "  Running: $($test.Name)" -ForegroundColor $CYAN
    Write-Host "  File:    $($test.Path)" -ForegroundColor $CYAN
    Write-Host ("─" * 44) -ForegroundColor $CYAN
    Write-Host ""

    # Set offscreen mode for GUI tests
    $env:QT_QPA_PLATFORM = "offscreen"

    $startTime = Get-Date
    $output = & $VENV_PYTHON $testPath 2>&1
    $exitCode = $LASTEXITCODE
    $duration = (Get-Date) - $startTime

    if ($output) {
        Write-Host $output
    }

    $elapsed = "{0:N1}s" -f $duration.TotalSeconds

    if ($exitCode -eq 0) {
        Write-Host ""
        Write-Host "  [$([char]0x2714)] PASSED ($elapsed)" -ForegroundColor $GREEN
        $passed++
        $results += @{ Name = $test.Name; Status = "PASSED"; Duration = $elapsed }
    }
    else {
        Write-Host ""
        Write-Host "  [X] FAILED ($elapsed, exit code: $exitCode)" -ForegroundColor $RED
        $failed++
        $results += @{ Name = $test.Name; Status = "FAILED"; Duration = $elapsed }
    }
    Write-Host ""
}

# Summary
Write-Host ("=" * 65)
Write-Host "  SUMMARY" -ForegroundColor $CYAN
Write-Host ("=" * 65)
foreach ($r in $results) {
    $color = if ($r.Status -eq "PASSED") { $GREEN } else { $RED }
    Write-Host "    [$($r.Status)] $($r.Name) ($($r.Duration))" -ForegroundColor $color
}
Write-Host ""
$total = $passed + $failed
if ($failed -eq 0) {
    Write-Host "  All $total tests passed!" -ForegroundColor $GREEN
}
else {
    Write-Host "  $passed/$total passed, $failed failed" -ForegroundColor $(if ($passed -gt 0) { $YELLOW } else { $RED })
}
Write-Host ""
