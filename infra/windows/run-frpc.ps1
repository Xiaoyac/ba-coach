$ErrorActionPreference = "Continue"
$frpRoot = "C:\Users\20640\AppData\Local\BA Coach\frp"

while ($true) {
  & "$frpRoot\0.71.0\frpc.exe" -c "$frpRoot\frpc.toml"
  Add-Content -LiteralPath (Join-Path $frpRoot "frpc-supervisor.log") `
    -Value "[$(Get-Date -Format o)] frpc exited; restarting in 5 seconds"
  Start-Sleep -Seconds 5
}
