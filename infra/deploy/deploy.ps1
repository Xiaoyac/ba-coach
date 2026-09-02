param(
    [string]$Server = "root@8.134.178.40",
    [string]$IdentityFile = "C:\Users\20640\.ssh\bacoach_deploy_ed25519",
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ReleaseId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$Archive = Join-Path $env:TEMP "bacoach-$ReleaseId.tar.gz"
$RemoteArchive = "/tmp/bacoach-$ReleaseId.tar.gz"
$RemoteDeployScript = "/tmp/bacoach-server-deploy-$ReleaseId.sh"

if (-not $SkipChecks) {
    Push-Location (Join-Path $ProjectRoot "frontend")
    try {
        npm run typecheck
    }
    finally {
        Pop-Location
    }

    Push-Location (Join-Path $ProjectRoot "backend")
    try {
        & ".\.venv\Scripts\python.exe" -m pytest -q
    }
    finally {
        Pop-Location
    }
}

try {
    Push-Location $ProjectRoot
    try {
        # Explicit allow-list: this cannot accidentally include .env,
        # Windows virtualenvs, caches, logs, or node_modules.
        $PackageItems = @(
            "backend/app",
            "backend/scripts",
            "backend/requirements.txt",
            "KnowledgeBase",
            "frontend/app",
            "frontend/components",
            "frontend/lib",
            "frontend/package.json",
            "frontend/package-lock.json",
            "frontend/next.config.mjs",
            "frontend/next-env.d.ts",
            "frontend/postcss.config.mjs",
            "frontend/tsconfig.json"
        )
        if (Test-Path "frontend/public") {
            $PackageItems += "frontend/public"
        }

        tar -czf $Archive $PackageItems
        if ($LASTEXITCODE -ne 0) { throw "tar failed" }

        $Forbidden = tar -tf $Archive | Select-String `
            '(^|/)(\.env$|\.venv/|node_modules/|\.next/|runtime-logs/|\.pytest_cache/)'
        if ($Forbidden) {
            throw "archive contains a forbidden secret or generated path"
        }
    }
    finally {
        Pop-Location
    }

    # Cross-region SSH occasionally times out during the first handshake even
    # though the Guangzhou host is healthy. Retry only the idempotent upload;
    # never blindly retry the release switch itself.
    $Uploaded = $false
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        scp -i $IdentityFile -o BatchMode=yes -o ConnectTimeout=25 `
            $Archive "${Server}:$RemoteArchive"
        if ($LASTEXITCODE -eq 0) {
            $Uploaded = $true
            break
        }
        if ($Attempt -lt 3) {
            Write-Warning "upload attempt $Attempt failed; retrying in 3 seconds"
            Start-Sleep -Seconds 3
        }
    }
    if (-not $Uploaded) { throw "upload failed after 3 attempts" }

    # Keep the privileged release runner versioned with the project. This is
    # especially important for additive schema migrations that must run before
    # the new ORM code starts selecting its new columns.
    scp -i $IdentityFile -o BatchMode=yes -o ConnectTimeout=25 `
        (Join-Path $ProjectRoot "infra\deploy\server-deploy.sh") `
        "${Server}:$RemoteDeployScript"
    if ($LASTEXITCODE -ne 0) { throw "deploy-script upload failed" }
    ssh -i $IdentityFile -o BatchMode=yes $Server `
        "install -m 0755 '$RemoteDeployScript' /usr/local/sbin/bacoach-deploy-release && rm -f '$RemoteDeployScript'"
    if ($LASTEXITCODE -ne 0) { throw "deploy-script install failed" }

    ssh -i $IdentityFile -o BatchMode=yes $Server `
        "sudo /usr/local/sbin/bacoach-deploy-release '$ReleaseId' '$RemoteArchive'"
    if ($LASTEXITCODE -ne 0) { throw "remote deployment failed" }

    curl.exe --fail --silent --show-error https://bacoach.xyz/ --output NUL
    if ($LASTEXITCODE -ne 0) { throw "public HTTPS verification failed" }

    Write-Host "BA Coach $ReleaseId deployed successfully." -ForegroundColor Green
    Write-Host "Open https://bacoach.xyz/"
}
finally {
    Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
}
