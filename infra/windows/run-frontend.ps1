$ErrorActionPreference = "Continue"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$logDirectory = Join-Path $projectRoot "runtime-logs"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$env:NODE_ENV = "production"

while ($true) {
  Push-Location "$projectRoot\frontend"
  try {
    & "D:\Node\npm.cmd" run start `
      1>> (Join-Path $logDirectory "frontend.out.log") `
      2>> (Join-Path $logDirectory "frontend.err.log")
  } finally {
    Pop-Location
  }
  Add-Content -LiteralPath (Join-Path $logDirectory "frontend.err.log") `
    -Value "[$(Get-Date -Format o)] frontend exited; restarting in 5 seconds"
  Start-Sleep -Seconds 5
}
