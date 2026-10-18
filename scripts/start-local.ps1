param([ValidateSet('start', 'stop', 'status', 'logs', 'backup')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

function Compose {
    & docker compose --project-name stockanalyzer-local --env-file .local/.env --file compose.local.yaml @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed. See the message above and docs/local-setup.md." }
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install Docker Desktop using the WSL 2 backend, start it, then launch again. See docs/local-setup.md and https://docs.docker.com/desktop/setup/install/windows-install/'
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required. See docs/local-setup.md.' }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop in Linux containers mode, then run this launcher again.' }
    New-Item -ItemType Directory -Force .local | Out-Null
    if (-not (Test-Path .local/.env)) {
        if ($Action -ne 'start') { throw 'Run the launcher with start first.' }
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $password = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
        $port = if ($env:STOCK_LOCAL_PORT) { $env:STOCK_LOCAL_PORT } else { '8501' }
        @("LOCAL_DB_PASSWORD=$password", "LOCAL_PORT=$port", 'FINNHUB_KEY=', 'TWELVEDATA_KEY=') |
            Set-Content -Encoding ASCII .local/.env
        Remove-Variable password, bytes
        # Limit generated secrets to this Windows user.
        $acl = Get-Acl .local/.env
        $acl.SetAccessRuleProtection($true, $false)
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl .local/.env $acl
    }
    $env:LOCAL_APP_VERSION = 'development'
    if ((Test-Path .git) -and (Get-Command git -ErrorAction SilentlyContinue)) {
        $revision = & git rev-parse --verify HEAD 2>$null
        if ($LASTEXITCODE -eq 0) {
            $env:LOCAL_APP_VERSION = $revision.Trim()
            if (& git status --porcelain --untracked-files=normal) { $env:LOCAL_APP_VERSION += '+local' }
        }
    }
    switch ($Action) {
        'start' {
            Compose up -d --build --wait --wait-timeout 180
            Compose exec app python -m scripts.bootstrap_admin
            $portLine = Get-Content .local/.env | Where-Object { $_ -match '^LOCAL_PORT=' } | Select-Object -First 1
            $port = if ($portLine) { $portLine.Substring('LOCAL_PORT='.Length).Trim() } else { '8501' }
            $url = "http://localhost:$port"
            Write-Host "App ready: $url"
            Write-Host 'Create teammates through Users in the app. Stop with Start-Windows.cmd stop.'
            Start-Process $url
        }
        'stop' { Compose stop }
        'status' { Compose ps }
        'logs' { Compose logs --tail 100 app radar-worker }
        'backup' {
            New-Item -ItemType Directory -Force .local/backups | Out-Null
            $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
            Compose exec -T db pg_dump -U stockapp -d stockanalyzer -Fc -f /tmp/local-backup.dump
            Compose cp "db:/tmp/local-backup.dump" ".local/backups/stockanalyzer-$stamp.dump"
            Compose exec -T db rm -f /tmp/local-backup.dump
            Write-Host "Backup saved: .local/backups/stockanalyzer-$stamp.dump"
        }
    }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
