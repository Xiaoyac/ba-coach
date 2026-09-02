$ErrorActionPreference = "Continue"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$logDirectory = Join-Path $projectRoot "runtime-logs"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

while ($true) {
  Push-Location "$projectRoot\backend"
  try {
    & "$projectRoot\backend\.venv\Scripts\python.exe" `
      -m uvicorn app.main:app --host 127.0.0.1 --port 8000 `
      1>> (Join-Path $logDirectory "backend.out.log") `
      2>> (Join-Path $logDirectory "backend.err.log")
  } finally {
    Pop-Location
  }
  Add-Content -LiteralPath (Join-Path $logDirectory "backend.err.log") `
    -Value "[$(Get-Date -Format o)] backend exited; restarting in 5 seconds"
  Start-Sleep -Seconds 5
}
