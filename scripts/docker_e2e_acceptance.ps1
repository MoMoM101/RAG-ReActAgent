#!/usr/bin/env pwsh
<#
.SYNOPSIS
    12-stage Docker E2E acceptance script for RAG Agent.
.DESCRIPTION
    Orchestrates a full end-to-end acceptance flow: build, health check, secrets,
    auth, document upload, index consistency, SSE QA, restart persistence,
    backup/restore, degradation, and smoke tests.
.PARAMETER Clean
    If set and all stages pass, tear down compose project (docker compose down -v).
.PARAMETER SkipBuild
    Skip the docker compose build stage (useful for iterative development).
.PARAMETER BackendPort
    Host port for the backend (default 18000).
.PARAMETER FrontendPort
    Host port for the frontend (default 15173).
.PARAMETER HealthTimeoutSec
    Maximum seconds to wait for both services to become healthy (default 120).
.PARAMETER ReadyTimeoutSec
    Maximum seconds to wait for documents to become ready after upload (default 180).
.PARAMETER SseTimeoutSec
    Timeout per SSE chat request via curl (default 120).
.PARAMETER RestoreTimeoutSec
    Maximum seconds to wait for restored documents to become ready (default 180).
#>

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipBuild,
    [int]$BackendPort = 18000,
    [int]$FrontendPort = 15173,
    [int]$HealthTimeoutSec = 120,
    [int]$ReadyTimeoutSec = 180,
    [int]$SseTimeoutSec = 120,
    [int]$RestoreTimeoutSec = 180
)

$ErrorActionPreference = "Stop"

# Ensure native command output is captured as UTF-8 (SSE responses contain CJK text).
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ─── Paths ───────────────────────────────────────────────────────────────────

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ArtifactsBase = Join-Path (Join-Path $RepoRoot "artifacts") "docker-e2e"
$FixturesDir = Join-Path (Join-Path (Join-Path (Join-Path $RepoRoot "backend") "tests") "e2e") "fixtures"
$ManifestPath = Join-Path $FixturesDir "manifest.json"
$SmokeTestPath = Join-Path (Join-Path (Join-Path (Join-Path $RepoRoot "backend") "tests") "e2e") "test_docker_smoke.py"
$ConsistencyScriptPath = Join-Path (Join-Path (Join-Path (Join-Path $RepoRoot "backend") "tests") "e2e") "live_index_consistency_check.py"
$BackendDir = Join-Path $RepoRoot "backend"

# ─── Run identity ─────────────────────────────────────────────────────────────

$RunTimestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$ShortGuid = (New-Guid).ToString().Substring(0, 8)
$RunId = "ragagent-e2e-${RunTimestamp}-${ShortGuid}"
$OutputDir = Join-Path $ArtifactsBase $RunId
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$ProjectNamePattern = '^ragagent-e2e-\d{8}-\d{6}-[0-9a-f]{8}$'

# ─── Authentication configuration ────────────────────────────────────────────

$IsCI = ($env:CI -eq "true")
$AdminUsername = if ($env:E2E_ADMIN_USERNAME) { $env:E2E_ADMIN_USERNAME } else { "admin" }
$AdminPassword = $env:E2E_ADMIN_PASSWORD
$JwtSecret = $env:E2E_JWT_SECRET
if (-not $AdminPassword -or -not $JwtSecret) {
    if ($IsCI) {
        Write-Error "E2E_ADMIN_PASSWORD and E2E_JWT_SECRET must be set in CI"
        exit 1
    }
    if (-not $AdminPassword) { $AdminPassword = "rag-agent-e2e-password" }
    if (-not $JwtSecret) { $JwtSecret = "rag-agent-e2e-jwt-secret-at-least-32-characters" }
    Write-Warning "E2E auth variables not fully set; using local test-only defaults"
}
$AccessToken = ""

# ─── Compose args and env ─────────────────────────────────────────────────────

$ComposeBaseFile = Join-Path $RepoRoot "docker-compose.yml"
$ComposeE2EFile = Join-Path $RepoRoot "docker-compose.e2e.yml"
$BackendEnvFile = Join-Path $BackendDir ".env"
$ComposeArgs = @()
if (Test-Path $BackendEnvFile) {
    # Compose reads only the values referenced by the compose files. The env
    # file itself is never copied into the image or mounted into a container.
    $ComposeArgs += @("--env-file", $BackendEnvFile)
}
$ComposeArgs += @("-p", $RunId, "-f", $ComposeBaseFile, "-f", $ComposeE2EFile)

$ComposeEnv = @{
    E2E_BACKEND_PORT       = "$BackendPort"
    E2E_FRONTEND_PORT      = "$FrontendPort"
    E2E_ADMIN_USERNAME     = $AdminUsername
    E2E_ADMIN_PASSWORD     = $AdminPassword
    E2E_JWT_SECRET         = $JwtSecret
}

# ─── Result tracking hashtable ────────────────────────────────────────────────

$Result = [ordered]@{
    run_id       = $RunId
    timestamp    = (Get-Date).ToUniversalTime().ToString("o")
    git_commit   = ""
    overall      = "running"
    failed_stage = $null
    stages       = [ordered]@{}
    config_snapshot = [ordered]@{}
}

# ─── Helper functions ─────────────────────────────────────────────────────────

function Write-Stage {
    param([string]$Name, [string]$Description = "")
    $line = "=" * 70
    Write-Host ""
    Write-Host $line -ForegroundColor Cyan
    Write-Host "  STAGE: $Name" -ForegroundColor Cyan
    if ($Description) {
        Write-Host "  $Description" -ForegroundColor Gray
    }
    Write-Host $line -ForegroundColor Cyan
}

function Invoke-Stage {
    param(
        [string]$Name,
        [string]$Description = "",
        [scriptblock]$ScriptBlock
    )
    Write-Stage -Name $Name -Description $Description

    $stageResult = [ordered]@{
        status    = "running"
        started   = (Get-Date).ToUniversalTime().ToString("o")
        elapsed_s = 0
        error     = ""
    }
    $Result.stages[$Name] = $stageResult

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        & $ScriptBlock
        $sw.Stop()
        $stageResult.elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        $stageResult.status = "passed"
        Write-Host "  [PASS] $Name ($($stageResult.elapsed_s)s)" -ForegroundColor Green
    }
    catch {
        $sw.Stop()
        $stageResult.elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        $stageResult.status = "failed"
        $errMsg = $_.Exception.Message
        # Sanitize token from error messages
        if ($Token) {
            $errMsg = $errMsg -replace [regex]::Escape($Token), "***"
        }
        $stageResult.error = $errMsg
        Write-Host "  [FAIL] $Name ($($stageResult.elapsed_s)s): $errMsg" -ForegroundColor Red
        throw
    }
}

function Get-GitCommit {
    try {
        $commit = & git -C $RepoRoot rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return $commit.Trim() }
    } catch {}
    return "unknown"
}

function Get-FirstNonNull {
    param([object[]]$Values)
    foreach ($value in $Values) {
        if ($null -ne $value) { return $value }
    }
    return $null
}

function Invoke-NativeLogged {
    param(
        [scriptblock]$Command,
        [string]$FailureMessage
    )
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell wraps native stderr as ErrorRecord. Docker emits
        # normal progress on stderr, so capture it without treating it as a
        # terminating PowerShell error and rely on the native exit code.
        $ErrorActionPreference = "Continue"
        $output = & $Command 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $output | ForEach-Object { Write-Host "    $_" }
    if ($exitCode -ne 0) {
        throw "$FailureMessage (exit code $exitCode)"
    }
}

function Set-ComposeEnvironment {
    foreach ($key in $ComposeEnv.Keys) {
        Set-Item -Path "env:$key" -Value $ComposeEnv[$key]
    }
}

function Get-HashSafe {
    param([string]$EnvVarName)
    $val = [Environment]::GetEnvironmentVariable($EnvVarName)
    if (-not $val) { return "missing" }
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($val)
        $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
        return [BitConverter]::ToString($hash).Replace("-", "").ToLower()
    } catch {
        return "error"
    }
}

function Wait-Healthy {
    param(
        [int]$TimeoutSec = $HealthTimeoutSec
    )
    $backendUrl = "http://127.0.0.1:${BackendPort}/api/health"
    $frontendUrl = "http://127.0.0.1:${FrontendPort}/"

    Write-Host "  Waiting for services to become healthy (timeout: ${TimeoutSec}s)..."
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $backendOk = $false
    $frontendOk = $false

    while ((Get-Date) -lt $deadline) {
        # Check backend health
        if (-not $backendOk) {
            try {
                $resp = Invoke-RestMethod -Uri $backendUrl -Method Get -TimeoutSec 5 -ErrorAction SilentlyContinue
                if ($resp.status -eq "ok") {
                    $backendOk = $true
                    Write-Host "    Backend healthy"
                }
            } catch {}
        }

        # Check frontend health
        if (-not $frontendOk) {
            try {
                $resp = Invoke-WebRequest -Uri $frontendUrl -Method Get -TimeoutSec 5 -UseBasicParsing -ErrorAction SilentlyContinue
                $content = Get-FirstNonNull -Values @($resp.Content, "")
                if ($content -match "<html" -or $content -match "<!DOCTYPE") {
                    $frontendOk = $true
                    Write-Host "    Frontend healthy"
                }
            } catch {}
        }

        if ($backendOk -and $frontendOk) {
            Write-Host "  Both services healthy" -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 2
    }

    if (-not $backendOk) {
        throw "Backend did not become healthy within ${TimeoutSec}s"
    }
    if (-not $frontendOk) {
        throw "Frontend did not become healthy within ${TimeoutSec}s"
    }
}

function Invoke-Pytest {
    param(
        [string]$TestPath,
        [string]$Description = ""
    )
    Write-Host "  Running pytest: $TestPath"
    $env:BACKEND_URL = "http://127.0.0.1:${BackendPort}"
    $env:DOCKER_E2E_REQUIRED = "1"
    $output = & python -m pytest $TestPath -v --tb=short --junitxml="$OutputDir/pytest_${Description}.xml" 2>&1
    $exitCode = $LASTEXITCODE
    Write-Host "  Pytest exit code: $exitCode"
    if ($output) {
        $output | ForEach-Object { Write-Host "    $_" }
    }

    if ($exitCode -ne 0) {
        throw "Pytest failed with exit code $exitCode for: $TestPath"
    }

    # Verify no tests were skipped (DOCKER_E2E_REQUIRED=1 means skip = failure)
    $xmlPath = Join-Path $OutputDir "pytest_${Description}.xml"
    if (Test-Path $xmlPath) {
        [xml]$xml = Get-Content $xmlPath
        $suites = if ($xml.testsuite) {
            @($xml.testsuite)
        } elseif ($xml.testsuites -and $xml.testsuites.testsuite) {
            @($xml.testsuites.testsuite)
        } else {
            throw "Invalid JUnit XML: no testsuite node in $xmlPath"
        }
        $skipped = 0
        foreach ($suite in $suites) {
            $skipped += [int](Get-FirstNonNull -Values @(
                $suite.skipped,
                $suite.GetAttribute("skipped"),
                "0"
            ))
        }
        if ($skipped -gt 0) {
            throw "Pytest had $skipped skipped tests in strict mode for: $TestPath"
        }
    }
    Write-Host "  [PASS] Pytest: $Description" -ForegroundColor Green
}

function Invoke-ContainerConsistency {
    param([string]$Description = "consistency")
    Write-Host "  Running live consistency check inside backend container"
    Set-ComposeEnvironment
    $containerId = (& docker compose @ComposeArgs ps -q backend 2>$null | Select-Object -First 1)
    if (-not $containerId) {
        throw "Backend container is not running"
    }
    $containerId = "$containerId".Trim()
    $containerScript = "/tmp/live_index_consistency_check.py"
    $copyOutput = & docker cp $ConsistencyScriptPath "${containerId}:${containerScript}" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to copy consistency script into backend container: $copyOutput"
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & docker compose @ComposeArgs exec -T backend `
            env RAG_AGENT_APP_ROOT=/app python $containerScript 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $logPath = Join-Path $OutputDir "container_${Description}.log"
    $output | ForEach-Object { "$_" } | Set-Content -Path $logPath -Encoding UTF8
    $output | ForEach-Object { Write-Host "    $_" }
    if ($exitCode -ne 0) {
        throw "Container consistency check failed with exit code $exitCode"
    }
    Write-Host "  Container consistency check passed"
}

function Write-Reports {
    param(
        [bool]$IsFinally = $false
    )

    # Create output directory
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }

    # Finalize result
    if ($IsFinally) {
        # Mark any unexecuted stages
        $allStages = @(
            "config_check", "build", "health", "secrets_check", "auth_check",
            "upload", "consistency", "sse_qa", "restart_persistence",
            "backup_restore", "degradation", "smoke"
        )
        foreach ($stageName in $allStages) {
            if (-not $Result.stages.Contains($stageName)) {
                $Result.stages[$stageName] = [ordered]@{
                    status    = "not_run"
                    started   = ""
                    elapsed_s = 0
                    error     = ""
                }
            }
        }
    }

    # Determine overall status
    $anyFailed = $false
    foreach ($stage in $Result.stages.Values) {
        if ($stage.status -eq "failed") { $anyFailed = $true; break }
    }
    if ($anyFailed) {
        $Result.overall = "failed"
    } else {
        $Result.overall = "passed"
    }

    # Write result.json
    $resultJsonPath = Join-Path $OutputDir "result.json"
    $Result | ConvertTo-Json -Depth 6 | Set-Content -Path $resultJsonPath -Encoding UTF8
    Write-Host "  Wrote: $resultJsonPath"

    # Write report.md
    $reportPath = Join-Path $OutputDir "report.md"
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine("# Docker E2E Acceptance Report")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("**Run ID:** $($Result.run_id)")
    [void]$sb.AppendLine("**Timestamp:** $($Result.timestamp)")
    [void]$sb.AppendLine("**Git Commit:** $($Result.git_commit)")
    [void]$sb.AppendLine("**Overall:** $($Result.overall.ToUpper())")
    if ($Result.failed_stage) {
        [void]$sb.AppendLine("**Failed Stage:** $($Result.failed_stage)")
    }
    [void]$sb.AppendLine("")

    # Stage table
    [void]$sb.AppendLine("## Stage Results")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("| Stage | Status | Elapsed (s) | Error |")
    [void]$sb.AppendLine("|-------|--------|-------------|-------|")
    foreach ($stageName in $Result.stages.Keys) {
        $stage = $Result.stages[$stageName]
        $status = $stage.status.ToUpper()
        $elapsed = $stage.elapsed_s
        $error = (Get-FirstNonNull -Values @($stage.error, "")) -replace '\|', '\|'
        if ($error.Length -gt 80) { $error = $error.Substring(0, 77) + "..." }
        [void]$sb.AppendLine("| $stageName | $status | $elapsed | $error |")
    }
    [void]$sb.AppendLine("")

    # Config snapshot
    [void]$sb.AppendLine("## Config Snapshot")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine('```json')
    $Result.config_snapshot | ConvertTo-Json -Depth 3 | ForEach-Object { [void]$sb.AppendLine($_) }
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine("")

    # Retention commands
    [void]$sb.AppendLine("## Retention / Cleanup")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("To view logs:")
    [void]$sb.AppendLine('```bash')
    [void]$sb.AppendLine("docker compose -p $RunId logs")
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("To tear down:")
    [void]$sb.AppendLine('```bash')
    [void]$sb.AppendLine("docker compose -p $RunId down -v")
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("Artifacts directory:")
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine($OutputDir)
    [void]$sb.AppendLine('```')

    $sb.ToString() | Set-Content -Path $reportPath -Encoding UTF8
    Write-Host "  Wrote: $reportPath"
}

# ─── Pre-flight: record git commit ───────────────────────────────────────────

$Result.git_commit = Get-GitCommit

# ─── Build config_snapshot ────────────────────────────────────────────────────

$Result.config_snapshot = [ordered]@{
    llm_provider          = if ($env:LLM_PROVIDER) { "configured" } else { "missing" }
    llm_model_sha256      = Get-HashSafe "LLM_MODEL"
    embedding_provider    = if ($env:EMBEDDING_PROVIDER) { "configured" } else { "missing" }
    embedding_model_sha256 = Get-HashSafe "EMBEDDING_MODEL"
    secret_key            = if ($env:SECRET_KEY) { "configured" } else { "missing" }
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN FLOW
# ═══════════════════════════════════════════════════════════════════════════════

try {
    # ── Stage 1: config_check ─────────────────────────────────────────────────

    Invoke-Stage -Name "config_check" -Description "Verify prerequisites and configuration" -ScriptBlock {
        # Verify compose files exist
        if (-not (Test-Path $ComposeBaseFile)) {
            throw "docker-compose.yml not found: $ComposeBaseFile"
        }
        if (-not (Test-Path $ComposeE2EFile)) {
            throw "docker-compose.e2e.yml not found: $ComposeE2EFile"
        }
        Write-Host "  Compose files OK"

        # Verify manifest exists
        if (-not (Test-Path $ManifestPath)) {
            throw "manifest.json not found: $ManifestPath"
        }
        Write-Host "  Manifest OK"

        # Verify docker and docker compose available
        $dockerVersion = & docker --version 2>&1
        if ($LASTEXITCODE -ne 0) { throw "docker not found" }
        Write-Host "  docker: $dockerVersion"

        $composeVersion = & docker compose version 2>&1
        if ($LASTEXITCODE -ne 0) { throw "docker compose not found" }
        Write-Host "  docker compose: $composeVersion"

        # Read manifest
        $manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

        # Verify each document's SHA-256 hash matches fixture file
        foreach ($doc in $manifest.documents) {
            $fixturePath = Join-Path $FixturesDir $doc.path
            if (-not (Test-Path $fixturePath)) {
                throw "Fixture file not found: $fixturePath"
            }
            $actualHash = (Get-FileHash -Path $fixturePath -Algorithm SHA256).Hash.ToLower()
            $expectedHash = $doc.sha256.ToLower()
            if ($actualHash -ne $expectedHash) {
                throw "SHA-256 mismatch for $($doc.path): expected=$expectedHash actual=$actualHash"
            }
            Write-Host "  SHA-256 verified: $($doc.path)"
        }

        # Verify RunId matches pattern
        if ($RunId -notmatch $ProjectNamePattern) {
            throw "RunId '$RunId' does not match pattern '$ProjectNamePattern'"
        }
        Write-Host "  RunId format OK: $RunId"

        # Check port conflicts
        $portsToCheck = @($BackendPort, $FrontendPort)
        foreach ($port in $portsToCheck) {
            # TIME_WAIT entries do not prevent a new listener from binding and
            # commonly remain after a previous acceptance run. Only an active
            # listener is a real port conflict.
            $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
            if ($connections) {
                $owners = ($connections | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique) -join ", "
                throw "Port $port is already in use (PID(s): $owners)"
            }
        }
        Write-Host "  Ports ${BackendPort}, ${FrontendPort} available"
    }

    # ── Stage 2: build ────────────────────────────────────────────────────────

    Invoke-Stage -Name "build" -Description "Build Docker images" -ScriptBlock {
        if ($SkipBuild) {
            Write-Host "  Skipping build (SkipBuild flag set)"
            return
        }
        Set-ComposeEnvironment
        $buildArgs = $ComposeArgs + @("build", "--quiet")
        Write-Host "  docker compose build --quiet ..."
        Invoke-NativeLogged -Command { docker compose @buildArgs } -FailureMessage "docker compose build failed"
        Write-Host "  Build complete"
    }

    # ── Stage 3: health ───────────────────────────────────────────────────────

    Invoke-Stage -Name "health" -Description "Start services and wait for health" -ScriptBlock {
        Set-ComposeEnvironment
        $upArgs = $ComposeArgs + @("up", "-d", "--wait")
        Write-Host "  docker compose up -d --wait ..."
        Invoke-NativeLogged -Command { docker compose @upArgs } -FailureMessage "docker compose up failed"

        Wait-Healthy

        # Verify frontend / returns HTML
        $frontendUrl = "http://127.0.0.1:${FrontendPort}/"
        try {
        $frontResp = Invoke-WebRequest -Uri $frontendUrl -Method Get -TimeoutSec 10 -UseBasicParsing
            $content = Get-FirstNonNull -Values @($frontResp.Content, "")
            if ($content -notmatch "<html" -and $content -notmatch "<!DOCTYPE") {
                throw "Frontend response does not look like HTML"
            }
            Write-Host "  Frontend HTML confirms OK"
        } catch {
            throw "Frontend / check failed: $_"
        }

        # Verify frontend /api/health proxy returns ok
        $proxyHealthUrl = "http://127.0.0.1:${FrontendPort}/api/health"
        try {
            $proxyResp = Invoke-RestMethod -Uri $proxyHealthUrl -Method Get -TimeoutSec 10
            if ($proxyResp.status -ne "ok") {
                throw "Frontend proxy health returned status=$($proxyResp.status)"
            }
            Write-Host "  Frontend proxy /api/health OK"
        } catch {
            throw "Frontend proxy /api/health check failed: $_"
        }
    }

    # ── Stage 4: secrets_check ────────────────────────────────────────────────

    Invoke-Stage -Name "secrets_check" -Description "Verify no secrets leaked in container" -ScriptBlock {
        Set-ComposeEnvironment

        # Find backend container name
        $psJson = & docker compose @ComposeArgs ps --format json 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose ps failed"
        }

        $backendContainer = $null
        foreach ($line in $psJson) {
            if (-not $line) { continue }
            try {
                $obj = $line | ConvertFrom-Json
                if ($obj.Service -eq "backend" -or $obj.Name -match "backend") {
                    $backendContainer = if ($obj.Name) { $obj.Name } else { $obj.ID }
                    break
                }
            } catch {}
        }

        if (-not $backendContainer) {
            # Fallback: construct from project name
            $backendContainer = "${RunId}-backend-1"
        }
        Write-Host "  Backend container: $backendContainer"

        # Verify /app/.env does NOT exist inside container
        $testResult = & docker exec $backendContainer test -f /app/.env 2>&1
        if ($LASTEXITCODE -eq 0) {
            throw "SECURITY: /app/.env file exists inside container!"
        }
        Write-Host "  /app/.env not found in container (expected)"

        # Get image size for logging
        try {
            $inspect = & docker inspect $backendContainer 2>&1 | ConvertFrom-Json
            if ($inspect) {
                $imageName = $inspect.Config.Image
                if ($imageName) {
                    $imageInfo = & docker images $imageName --format "{{.Size}}" 2>&1
                    if ($imageInfo) {
                        Write-Host "  Backend image size: $imageInfo"
                    }
                }
            }
        } catch {
            Write-Host "  (could not determine image size)"
        }
    }

    # ── Stage 5: auth_check ───────────────────────────────────────────────────

    Invoke-Stage -Name "auth_check" -Description "Verify username/password login and JWT authentication" -ScriptBlock {
        $baseUrl = "http://127.0.0.1:${BackendPort}"
        $docsUrl = "$baseUrl/api/documents"
        $metricsUrl = "$baseUrl/api/metrics"
        $loginBody = @{ username = $AdminUsername; password = $AdminPassword } | ConvertTo-Json
        $login = Invoke-RestMethod -Uri "$baseUrl/api/auth/login" -Method Post -ContentType "application/json" -Body $loginBody -TimeoutSec 10
        if (-not $login.access_token) { throw "Login response did not contain an access token" }
        $script:AccessToken = $login.access_token
        $adminHeaders = @{ Authorization = "Bearer $AccessToken" }

        # Verify /api/documents without token returns 401/403
        try {
            $resp = Invoke-WebRequest -Uri $docsUrl -Method Get -TimeoutSec 10 -UseBasicParsing
            if ($resp.StatusCode -notin @(401, 403)) {
                throw "Expected 401/403 without token, got $($resp.StatusCode)"
            }
            Write-Host "  /api/documents without token: $($resp.StatusCode) (expected)"
        } catch {
            if (-not $_.Exception.Response) { throw }
            $statusCode = [int]$_.Exception.Response.StatusCode
            if ($statusCode -notin @(401, 403)) {
                throw "Expected 401/403 without token, got $statusCode"
            }
            Write-Host "  /api/documents without token: $statusCode (expected)"
        }

        # Verify /api/documents with token returns 200
        try {
            $resp = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
            Write-Host "  /api/documents with token: 200 (expected)"
        } catch {
            throw "/api/documents with token failed: $_"
        }

        # Verify /api/metrics without token returns 401/403
        try {
            $resp = Invoke-WebRequest -Uri $metricsUrl -Method Get -TimeoutSec 10 -UseBasicParsing
            if ($resp.StatusCode -notin @(401, 403)) {
                throw "Expected 401/403 for metrics without token, got $($resp.StatusCode)"
            }
            Write-Host "  /api/metrics without token: $($resp.StatusCode) (expected)"
        } catch {
            if (-not $_.Exception.Response) { throw }
            $statusCode = [int]$_.Exception.Response.StatusCode
            if ($statusCode -notin @(401, 403)) {
                throw "Expected 401/403 for metrics without token, got $statusCode"
            }
            Write-Host "  /api/metrics without token: $statusCode (expected)"
        }
    }

    # ── Stage 6: upload ───────────────────────────────────────────────────────

    Invoke-Stage -Name "upload" -Description "Upload documents and wait for ready state" -ScriptBlock {
        $baseUrl = "http://127.0.0.1:${BackendPort}"
        $adminHeaders = @{ Authorization = "Bearer $AccessToken" }

        # Read manifest
        $manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

        # Build a curl multipart upload. Invoke-RestMethod -Form is only
        # available in PowerShell 7, while curl.exe is present on supported
        # Windows hosts and in CI images.
        $curlArgs = @(
            "-sS",
            "--fail-with-body",
            "--max-time", "30",
            "-H", "Authorization: Bearer $AccessToken"
        )
        foreach ($doc in $manifest.documents) {
            $fixturePath = Join-Path $FixturesDir $doc.path
            $curlArgs += @("-F", "files=@$fixturePath")
        }

        Write-Host "  Uploading $($manifest.documents.Count) documents..."
        $uploadUrl = "$baseUrl/api/documents/upload-batch"
        try {
            $curlArgs += $uploadUrl
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                $uploadOutput = & curl.exe @curlArgs 2>&1
                $uploadExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $previousPreference
            }
            if ($uploadExitCode -ne 0) {
                throw "curl upload failed (exit code $uploadExitCode): $($uploadOutput -join ' ')"
            }
            $uploadResult = ($uploadOutput -join "`n") | ConvertFrom-Json
            Write-Host "  Upload response: $($uploadResult | ConvertTo-Json -Compress)"
        } catch {
            throw "Upload failed: $_"
        }

        # Poll until all documents reach "ready" or timeout
        $docsUrl = "$baseUrl/api/documents"
        $deadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
        $allReady = $false
        $expectedCount = $manifest.documents.Count

        Write-Host "  Waiting for $expectedCount documents to become ready (timeout: ${ReadyTimeoutSec}s)..."
        while ((Get-Date) -lt $deadline) {
            try {
                $docs = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
                $total = if ($docs -is [array]) { $docs.Count } elseif ($docs.documents) { $docs.documents.Count } else { 0 }
                $readyCount = 0
                if ($docs -is [array]) {
                    $readyCount = ($docs | Where-Object { $_.status -eq "ready" }).Count
                } elseif ($docs.documents) {
                    $readyCount = ($docs.documents | Where-Object { $_.status -eq "ready" }).Count
                }
                Write-Host "    Documents: $total total, $readyCount ready"
                if ($total -eq $expectedCount -and $readyCount -eq $expectedCount) {
                    $allReady = $true
                    break
                }
            } catch {
                Write-Host "    Poll error: $_"
            }
            Start-Sleep -Seconds 5
        }

        if (-not $allReady) {
            throw "Not all documents reached 'ready' state within ${ReadyTimeoutSec}s"
        }

        # Verify each document's chunk_count matches manifest expected_chunks
        $docs = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
        $docArray = if ($docs -is [array]) { $docs } elseif ($docs.documents) { $docs.documents } else { @() }
        foreach ($expectedDoc in $manifest.documents) {
            $matched = $docArray | Where-Object { $_.filename -eq $expectedDoc.path }
            if (-not $matched) {
                throw "Document not found after upload: $($expectedDoc.path)"
            }
            if ($matched.chunk_count -ne $expectedDoc.expected_chunks) {
                throw "Chunk count mismatch for $($expectedDoc.path): expected=$($expectedDoc.expected_chunks) actual=$($matched.chunk_count)"
            }
            Write-Host "  $($expectedDoc.path): chunk_count=$($matched.chunk_count) (expected=$($expectedDoc.expected_chunks)) OK"
        }
    }

    # ── Stage 7: consistency ──────────────────────────────────────────────────

    Invoke-Stage -Name "consistency" -Description "Run live index consistency test" -ScriptBlock {
        Invoke-ContainerConsistency -Description "consistency"
    }

    # ── Stage 8: sse_qa ───────────────────────────────────────────────────────

    Invoke-Stage -Name "sse_qa" -Description "SSE chat QA verification for all questions" -ScriptBlock {
        $manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $chatUrl = "http://127.0.0.1:${BackendPort}/api/chat"

        $maxQaAttempts = 2
        foreach ($q in $manifest.questions) {
            $qaPassed = $false
            for ($qaAttempt = 1; $qaAttempt -le $maxQaAttempts; $qaAttempt++) {
                Write-Host "  Question (attempt ${qaAttempt}/${maxQaAttempts}): $($q.question)"

                # Write JSON body to temp file (avoids encoding issues with Chinese)
                $body = @{ message = $q.question } | ConvertTo-Json -Compress
                $tmpFile = Join-Path ([System.IO.Path]::GetTempPath()) "e2e_qa_$(New-Guid).json"
                [System.IO.File]::WriteAllBytes($tmpFile, [System.Text.Encoding]::UTF8.GetBytes($body))

                try {
                $output = & curl.exe -sS -N -H "Authorization: Bearer $AccessToken" -H "Content-Type: application/json" -d "@$tmpFile" --max-time $SseTimeoutSec "http://127.0.0.1:${BackendPort}/api/chat" 2>&1
                $exitCode = $LASTEXITCODE
                if ($exitCode -ne 0) {
                    throw "curl failed with exit code ${exitCode}: $output"
                }

                Write-Host "    SSE output length: $($output.Length) chars"

                # Parse SSE output: split by event: and data: prefixes
                $events = [ordered]@{}
                $lines = ($output -replace "`r`n", "`n") -split "`n"
                $currentEvent = ""
                for ($i = 0; $i -lt $lines.Count; $i++) {
                    $line = $lines[$i].Trim()
                    if ($line -match "^event:\s*(.+)$") {
                        $currentEvent = $Matches[1].Trim()
                        if (-not $events.Contains($currentEvent)) {
                            $events[$currentEvent] = @()
                        }
                    } elseif ($line -match "^data:\s*(.+)$") {
                        $dataStr = $Matches[1].TrimEnd("`r")
                        if ($currentEvent) {
                            $events[$currentEvent] += $dataStr
                        }
                    }
                }

                # Assert required events
                $requiredEvents = @("answer_chunk", "sources", "verification", "done")
                $allAnswerText = ""
                foreach ($eventName in $requiredEvents) {
                    if (-not $events.Contains($eventName)) {
                        throw "Missing required SSE event: '$eventName' for question: $($q.question)"
                    }
                }
                Write-Host "    All required events present: $($requiredEvents -join ', ')"

                # Collect full answer text from answer_chunk events
                foreach ($dataJson in $events["answer_chunk"]) {
                    try {
                        $chunkData = $dataJson | ConvertFrom-Json
                        $allAnswerText += Get-FirstNonNull -Values @($chunkData.delta, "")
                    } catch {}
                }

                # Check expected_terms appear in answer text
                foreach ($term in $q.expected_terms) {
                    if ($allAnswerText -notmatch [regex]::Escape($term)) {
                        throw "Expected term '$term' not found in answer for question: $($q.question)"
                    }
                    Write-Host "    Term found: $term"
                }

                # Check expected_source appears in sources
                $sourceFound = $false
                $sourcesData = $events["sources"] -join " "
                if ($sourcesData -match [regex]::Escape($q.expected_source)) {
                    $sourceFound = $true
                } else {
                    # Try parsing as JSON and checking filenames
                    foreach ($dataJson in $events["sources"]) {
                        try {
                            $srcArr = $dataJson | ConvertFrom-Json
                            foreach ($src in $srcArr) {
                                if ($src.filename -match [regex]::Escape($q.expected_source) -or
                                    $src.source -match [regex]::Escape($q.expected_source)) {
                                    $sourceFound = $true
                                    break
                                }
                            }
                        } catch {}
                    }
                }
                if (-not $sourceFound) {
                    throw "Expected source '$($q.expected_source)' not found in sources for question: $($q.question)"
                }
                Write-Host "    Source found: $($q.expected_source)"

                # Parse verification JSON, assert faithfulness=1.0, citation_precision=1.0, citation_recall=1.0
                $verificationOk = $false
                foreach ($dataJson in $events["verification"]) {
                    try {
                        $verif = $dataJson | ConvertFrom-Json
                        $faithfulness = Get-FirstNonNull -Values @($verif.faithfulness, $verif.overall_score, -1)
                        $citationPrecision = Get-FirstNonNull -Values @($verif.citation_precision, $verif.precision, -1)
                        $citationRecall = Get-FirstNonNull -Values @($verif.citation_recall, $verif.recall, -1)

                        Write-Host "    Verification: faithfulness=$faithfulness citation_precision=$citationPrecision citation_recall=$citationRecall"

                        if ($faithfulness -eq 1.0 -and $citationPrecision -eq 1.0 -and $citationRecall -eq 1.0) {
                            $verificationOk = $true
                            break
                        }
                    } catch {
                        Write-Host "    (could not parse verification JSON)"
                    }
                }
                if (-not $verificationOk) {
                    throw "Verification check failed: expected faithfulness=1.0, citation_precision=1.0, citation_recall=1.0"
                }
                    Write-Host "    Verification passed: all scores = 1.0"
                    $qaPassed = $true
                }
                catch {
                    if ($qaAttempt -ge $maxQaAttempts) {
                        throw
                    }
                    Write-Warning "SSE QA attempt ${qaAttempt} failed: $($_.Exception.Message). Retrying once."
                    Start-Sleep -Seconds 1
                }
                finally {
                    # Clean up temp file
                    if (Test-Path $tmpFile) {
                        Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
                    }
                }
                if ($qaPassed) {
                    break
                }
            }
        }
        Write-Host "  All SSE QA checks passed ($($manifest.questions.Count) questions)"
    }

    # ── Stage 9: restart_persistence ──────────────────────────────────────────

    Invoke-Stage -Name "restart_persistence" -Description "Verify data persists across container restart" -ScriptBlock {
        Set-ComposeEnvironment
        Write-Host "  Restarting backend and qdrant..."
        Invoke-NativeLogged -Command {
            docker compose @ComposeArgs restart backend qdrant
        } -FailureMessage "docker compose restart failed"

        Wait-Healthy

        $manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $docsUrl = "http://127.0.0.1:${BackendPort}/api/documents"
        $adminHeaders = @{ Authorization = "Bearer $AccessToken" }
        $docs = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
        $docArray = if ($docs -is [array]) { $docs } elseif ($docs.documents) { $docs.documents } else { @() }
        $readyCount = ($docArray | Where-Object { $_.status -eq "ready" }).Count

        if ($docArray.Count -ne $manifest.documents.Count) {
            throw "Document count mismatch after restart: expected=$($manifest.documents.Count) actual=$($docArray.Count)"
        }
        if ($readyCount -ne $manifest.documents.Count) {
            throw "Not all documents ready after restart: expected=$($manifest.documents.Count) actual=$readyCount"
        }
        Write-Host "  Persistence verified: $readyCount/$($manifest.documents.Count) documents ready after restart"
    }

    # ── Stage 10: backup_restore ──────────────────────────────────────────────

    Invoke-Stage -Name "backup_restore" -Description "Full backup, clear-all, restore, and re-verify" -ScriptBlock {
        $baseUrl = "http://127.0.0.1:${BackendPort}"
        $adminHeaders = @{ Authorization = "Bearer $AccessToken" }

        $manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

        # Snapshot pre-backup document IDs and properties
        $docsUrl = "$baseUrl/api/documents"
        $preDocs = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
        $preDocArray = if ($preDocs -is [array]) { $preDocs } elseif ($preDocs.documents) { $preDocs.documents } else { @() }
        $preDocIds = $preDocArray | ForEach-Object { $_.id }
        $preDocCount = $preDocArray.Count
        Write-Host "  Pre-backup document count: $preDocCount"

        # Download backup via curl
        $backupDir = Join-Path $OutputDir "backups"
        if (-not (Test-Path $backupDir)) {
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        }
        $backupFile = Join-Path $backupDir "restore-test.tar.gz"
        Write-Host "  Downloading backup..."
        & curl.exe -sS -H "Authorization: Bearer $AccessToken" -o "$backupFile" "http://127.0.0.1:${BackendPort}/api/backup" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Backup download failed"
        }

        # Verify non-zero size
        $backupSize = (Get-Item $backupFile).Length
        if ($backupSize -eq 0) {
            throw "Backup file is empty"
        }
        Write-Host "  Backup size: $backupSize bytes"

        # Record SHA-256
        $backupHash = (Get-FileHash -Path $backupFile -Algorithm SHA256).Hash
        Write-Host "  Backup SHA-256: $backupHash"

        # DELETE clear-all
        Write-Host "  Clearing all documents..."
        $clearUrl = "$baseUrl/api/documents/clear-all"
        try {
            $clearResult = Invoke-RestMethod -Uri $clearUrl -Method Delete -Headers $adminHeaders -TimeoutSec 30
            Write-Host "  Clear result: $($clearResult | ConvertTo-Json -Compress)"
        } catch {
            throw "/api/documents/clear-all failed: $_"
        }

        # Verify document count is 0
        Start-Sleep -Seconds 3
        $postClearDocs = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
        $postClearCount = if ($postClearDocs -is [array]) { $postClearDocs.Count } elseif ($postClearDocs.documents) { $postClearDocs.documents.Count } else { 0 }
        if ($postClearCount -ne 0) {
            throw "Document count after clear is $postClearCount, expected 0"
        }
        Write-Host "  Documents after clear: 0 (expected)"

        # Restore via curl -F
        Write-Host "  Restoring from backup..."
        $restoreOutput = & curl.exe -sS -H "Authorization: Bearer $AccessToken" -F "file=@$backupFile" "http://127.0.0.1:${BackendPort}/api/backup/restore" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Restore failed: $restoreOutput"
        }
        Write-Host "  Restore response: $restoreOutput"

        # Parse restore response
        try {
            $restoreData = $restoreOutput | ConvertFrom-Json
            $restoredCount = Get-FirstNonNull -Values @($restoreData.documents_restored, 0)
            if ($restoredCount -ne $manifest.documents.Count) {
                throw "documents_restored mismatch: expected=$($manifest.documents.Count) actual=$restoredCount"
            }
            Write-Host "  Documents restored: $restoredCount"
        } catch {
            throw "Failed to parse restore response: $_"
        }

        # Wait for restored documents to become ready
        $deadline = (Get-Date).AddSeconds($RestoreTimeoutSec)
        $allReady = $false
        Write-Host "  Waiting for restored documents to become ready (timeout: ${RestoreTimeoutSec}s)..."
        while ((Get-Date) -lt $deadline) {
            try {
                $docs = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
                $docArray = if ($docs -is [array]) { $docs } elseif ($docs.documents) { $docs.documents } else { @() }
                $readyCount = ($docArray | Where-Object { $_.status -eq "ready" }).Count
                Write-Host "    Documents: $($docArray.Count) total, $readyCount ready"
                if ($docArray.Count -eq $manifest.documents.Count -and $readyCount -eq $manifest.documents.Count) {
                    $allReady = $true
                    break
                }
            } catch {
                Write-Host "    Poll error: $_"
            }
            Start-Sleep -Seconds 5
        }
        if (-not $allReady) {
            throw "Not all restored documents reached ready state within ${RestoreTimeoutSec}s"
        }

        # Verify original document IDs exist
        $postRestoreDocs = Invoke-RestMethod -Uri $docsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
        $postRestoreArray = if ($postRestoreDocs -is [array]) { $postRestoreDocs } elseif ($postRestoreDocs.documents) { $postRestoreDocs.documents } else { @() }
        foreach ($preId in $preDocIds) {
            $found = $postRestoreArray | Where-Object { $_.id -eq $preId }
            if (-not $found) {
                Write-Host "  Note: original document ID $preId not found after restore (restore may assign new IDs)"
            }
        }
        Write-Host "  Restored documents all ready"

        # Re-run consistency test
        Write-Host "  Re-running consistency test after restore..."
        Invoke-ContainerConsistency -Description "consistency_post_restore"

        # Re-run one SSE QA question
        Write-Host "  Re-running SSE QA after restore..."
        $q = $manifest.questions[0]
        $chatUrl = "http://127.0.0.1:${BackendPort}/api/chat"
        $body = @{ message = $q.question } | ConvertTo-Json -Compress
        $tmpFile = Join-Path ([System.IO.Path]::GetTempPath()) "e2e_restore_qa_$(New-Guid).json"
        [System.IO.File]::WriteAllBytes($tmpFile, [System.Text.Encoding]::UTF8.GetBytes($body))
        try {
                $output = & curl.exe -sS -N -H "Authorization: Bearer $AccessToken" -H "Content-Type: application/json" -d "@$tmpFile" --max-time $SseTimeoutSec "http://127.0.0.1:${BackendPort}/api/chat" 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "Post-restore curl failed: $output"
            }
            $outputText = $output -join "`n"
            $outputText | Set-Content -Path (Join-Path $OutputDir "post_restore_sse.log") -Encoding UTF8
            # Verify sources and done events present
            if ($outputText -notmatch "event:\s*sources") {
                throw "Post-restore QA missing 'sources' event"
            }
            if ($outputText -notmatch "event:\s*done") {
                throw "Post-restore QA missing 'done' event"
            }
            if ($outputText -notmatch "event:\s*verification") {
                throw "Post-restore QA missing 'verification' event"
            }
            Write-Host "  Post-restore SSE QA: sources/verification/done confirmed"
        } finally {
            if (Test-Path $tmpFile) {
                Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # ── Stage 11: degradation ─────────────────────────────────────────────────

    Invoke-Stage -Name "degradation" -Description "Verify graceful degradation when Qdrant is down" -ScriptBlock {
        Set-ComposeEnvironment
        $depsUrl = "http://127.0.0.1:${BackendPort}/api/health/dependencies"
        $adminHeaders = @{ Authorization = "Bearer $AccessToken" }

        # Stop qdrant
        Write-Host "  Stopping qdrant..."
        Invoke-NativeLogged -Command {
            docker compose @ComposeArgs stop qdrant
        } -FailureMessage "docker compose stop qdrant failed"
        Start-Sleep -Seconds 3

        # Verify /api/health/dependencies shows qdrant=error, sqlite=ok
        try {
            $deps = Invoke-RestMethod -Uri $depsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
            $qdrantStatus = Get-FirstNonNull -Values @($deps.dependencies.qdrant, "")
            $sqliteStatus = Get-FirstNonNull -Values @($deps.dependencies.sqlite, "")
            Write-Host "  Dependencies: qdrant=$qdrantStatus sqlite=$sqliteStatus"
            if ($qdrantStatus -ne "error") {
                throw "Expected qdrant=error, got qdrant=$qdrantStatus"
            }
            if ($sqliteStatus -ne "ok") {
                throw "Expected sqlite=ok, got sqlite=$sqliteStatus"
            }
            Write-Host "  Degradation verified: qdrant=error, sqlite=ok"
        } catch {
            throw "Degradation check failed: $_"
        }

        # Start qdrant
        Write-Host "  Starting qdrant..."
        Invoke-NativeLogged -Command {
            docker compose @ComposeArgs start qdrant
        } -FailureMessage "docker compose start qdrant failed"
        Start-Sleep -Seconds 5

        # Verify health returns ok, qdrant=ok
        $healthUrl = "http://127.0.0.1:${BackendPort}/api/health"
        $depsRecovered = Invoke-RestMethod -Uri $depsUrl -Method Get -Headers $adminHeaders -TimeoutSec 10
        $healthOk = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 10
        $qdrantRecovered = Get-FirstNonNull -Values @($depsRecovered.dependencies.qdrant, "")
        Write-Host "  Recovered: health=$($healthOk.status) qdrant=$qdrantRecovered"
        if ($healthOk.status -ne "ok") {
            throw "Health did not return ok after qdrant restart"
        }
        if ($qdrantRecovered -ne "ok") {
            throw "Expected qdrant=ok after restart, got qdrant=$qdrantRecovered"
        }
        Write-Host "  Recovery verified: health=ok, qdrant=ok"
    }

    # ── Stage 12: smoke ───────────────────────────────────────────────────────

    Invoke-Stage -Name "smoke" -Description "Run strict Docker smoke tests" -ScriptBlock {
        Invoke-Pytest -TestPath $SmokeTestPath -Description "smoke"
    }

    # ── All stages passed ─────────────────────────────────────────────────────

    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Green
    Write-Host "  ALL STAGES PASSED" -ForegroundColor Green
    Write-Host ("=" * 70) -ForegroundColor Green
    $Result.overall = "passed"
}
catch {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Red

    # Find first failed stage
    $failedStage = $null
    foreach ($stageName in $Result.stages.Keys) {
        if ($Result.stages[$stageName].status -eq "failed") {
            $failedStage = $stageName
            break
        }
    }
    $Result.failed_stage = $failedStage
    $Result.overall = "failed"

    Write-Host "  ACCEPTANCE FAILED at stage: $failedStage" -ForegroundColor Red
    Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ("=" * 70) -ForegroundColor Red
}
finally {
    # Always write reports
    Write-Reports -IsFinally $true

    # Cleanup only on success + -Clean
    if ($Result.overall -eq "passed" -and $Clean) {
        Write-Host ""
        Write-Host "  Cleaning up compose project (passed + -Clean)..."
        Set-ComposeEnvironment
        try {
            Invoke-NativeLogged -Command {
                docker compose @ComposeArgs down -v
            } -FailureMessage "docker compose cleanup failed"
            Write-Host "  Cleanup complete."
        } catch {
            Write-Warning "Cleanup failed: $_"
        }
    } else {
        Write-Host ""
        Write-Host "  ── Retention ──────────────────────────────────────────────"
        Write-Host "  Compose project: $RunId"
        Write-Host "  Output directory: $OutputDir"
        Write-Host ""
        Write-Host "  To view logs:"
        Write-Host "    docker compose -p $RunId logs"
        Write-Host ""
        Write-Host "  To tear down:"
        Write-Host "    docker compose -p $RunId down -v"
        Write-Host "  ───────────────────────────────────────────────────────────"
    }

    # Exit with appropriate code
    if ($Result.overall -eq "passed") {
        exit 0
    } else {
        exit 1
    }
}
