[CmdletBinding()]
param([ValidateRange(2, 42)][int]$WaitMinutes = 34)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows') {
    throw 'Run this installer check only on a GitHub-hosted ephemeral Windows runner.'
}

$releaseUri = 'https://github.com/skanfdt/KioskAI/releases/download/windows-2026-11-21-r2/KioskAI-Setup.exe'
$releaseHash = '563f548bff471d0acaf4b5f857b4e7a58d584f2e4e5700dba68f0e461e4034ae'
$modelName = 'qwen3.5:9b'
$ollamaUrl = 'http://127.0.0.1:11434'
$kioskRoot = Join-Path $env:LOCALAPPDATA 'KioskAI'
$modelRoot = Join-Path $env:USERPROFILE '.ollama/models'
$ollamaRoot = Join-Path $env:LOCALAPPDATA 'Programs/Ollama'
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'KioskAI.lnk'
$privateDir = Join-Path $env:RUNNER_TEMP ('kiosk-fresh-' + [guid]::NewGuid().ToString('N'))
$evidenceDir = if ($env:EVIDENCE_DIR) { $env:EVIDENCE_DIR } else { Join-Path $env:RUNNER_TEMP 'kiosk-evidence' }
$null = New-Item -ItemType Directory -Force -Path $privateDir, $evidenceDir
$outFile = Join-Path $privateDir 'setup.stdout'
$errFile = Join-Path $privateDir 'setup.stderr'
$setupFile = Join-Path $privateDir 'KioskAI-Setup.exe'
$timer = [Diagnostics.Stopwatch]::StartNew()
$result = [ordered]@{
    passed = $false
    stage = 'preflight'
    utcStarted = [DateTime]::UtcNow.ToString('o')
    runnerImage = $env:ImageOS
    runnerImageVersion = $env:ImageVersion
    windowsBuild = [Environment]::OSVersion.Version.ToString()
    preflight = [ordered]@{}
    setup = [ordered]@{ release = $releaseUri; sha256Expected = $releaseHash; arguments = @() }
    after = [ordered]@{}
    secondsFromStart = [ordered]@{}
}

function Save-Evidence {
    $result.elapsedSeconds = [int]$timer.Elapsed.TotalSeconds
    $tempEvidence = Join-Path $evidenceDir 'install.partial'
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tempEvidence -Encoding utf8NoBOM
    [IO.File]::Move($tempEvidence, (Join-Path $evidenceDir 'install.json'), $true)
}

function Mark-Time([string]$Name) {
    if (!$result.secondsFromStart.Contains($Name)) {
        $result.secondsFromStart[$Name] = [int]$timer.Elapsed.TotalSeconds
        Write-Host ('[{0}s] {1}' -f $result.secondsFromStart[$Name], $Name)
    }
}

function Get-OllamaEntries {
    $uninstallKeys = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    Get-ItemProperty -Path $uninstallKeys -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match '(?i)\bOllama\b' }
}

function Get-ModelFiles {
    if (Test-Path -LiteralPath $modelRoot) {
        Get-ChildItem -LiteralPath $modelRoot -Recurse -File -Force
    }
}

function Read-SharedText([string]$Path, [int]$Offset = 0) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    try {
        $file = [IO.File]::Open($Path, 'Open', 'Read', [IO.FileShare]'ReadWrite, Delete')
        try {
            if ($file.Length -le $Offset) { return '' }
            $file.Position = $Offset
            $reader = [IO.StreamReader]::new($file, [Text.Encoding]::UTF8)
            try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
        } finally { $file.Dispose() }
    } catch [IO.IOException] { return '' }
}

function Get-Publisher([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $sig = Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction SilentlyContinue
    return $sig -and $sig.Status -eq 'Valid' -and $sig.SignerCertificate -and
        $sig.SignerCertificate.GetNameInfo('SimpleName', $false) -ceq 'Ollama Inc.'
}

function Fetch-Release {
    # The public release can redirect only to GitHub's own HTTPS asset storage.
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(4)
    $client.DefaultRequestHeaders.UserAgent.ParseAdd('KioskAI-release-verification/1.0')
    try {
        $uri = [uri]$releaseUri
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            if ($uri.Scheme -ne 'https' -or !$uri.IsDefaultPort -or $uri.UserInfo -or
                $uri.Host -notin @('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com')) {
                throw 'Unexpected GitHub download origin.'
            }
            $response = $client.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            try {
                if ([int]$response.StatusCode -in @(301,302,303,307,308)) {
                    $uri = [uri]::new($uri, $response.Headers.Location)
                    continue
                }
                $result.setup.downloadHttp = [int]$response.StatusCode
                $null = $response.EnsureSuccessStatusCode()
                $file = [IO.File]::Open($setupFile, 'CreateNew', 'Write', 'None')
                try {
                    $cts = [Threading.CancellationTokenSource]::new([TimeSpan]::FromMinutes(5))
                    try { $response.Content.CopyToAsync($file, $cts.Token).GetAwaiter().GetResult() } finally { $cts.Dispose() }
                } finally { $file.Dispose() }
                return
            } finally { $response.Dispose() }
        }
        throw 'Too many release redirects.'
    } finally { $client.Dispose() }
}

function Get-SafeFailure([string]$raw) {
    # Publish known error types and numeric codes. Paths, URIs and arbitrary output stay private.
    $patterns = [ordered]@{
        'disk-full' = 'not enough space|disk full|WinError 112|공간이 부족'
        'insufficient-memory' = 'requires more system memory|out of memory|not enough memory|가용 메모리'
        'ollama-download-signature-or-install-failed' = 'CalledProcessError|returned non-zero exit status'
        'model-server-unavailable' = '로컬 Ollama가 준비되지|설치된 Ollama 실행 파일'
        'model-vision-unavailable' = '이미지 입력 지원'
        'download-error' = '다운로드 주소|connection reset|timed out|URL error|SSL'
        'desktop-display-error' = 'display|desktop|pyautogui|win32gui|화면'
    }
    $codes = @([regex]::Matches($raw, '(?i)\b(?:HTTP Error |HTTP/1\.[01]" |WinError |Errno |exit status )\d{1,8}\b') | ForEach-Object { $_.Value } | Select-Object -Unique -Last 4)
    $labels = @($patterns.Keys | Where-Object { $raw -match $patterns[$_] })
    return [ordered]@{ present = [bool]$raw.Trim(); kinds = $labels; numericCodes = $codes }
}

try {
    $chromePaths = @(
        (Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google/Chrome/Application/chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google/Chrome/Application/chrome.exe')
    )
    $chrome = $chromePaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    $binaryPaths = @(
        (Join-Path $ollamaRoot 'ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama/ollama.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Ollama/ollama.exe')
    )
    $overrideNames = @('OLLAMA_HOST','OLLAMA_MODELS','KIOSKAI_OLLAMA_PORT','KIOSKAI_MODEL_PROVIDER')
    $overrides = @($overrideNames | Where-Object {
        $name = $_
        @('Process','User','Machine') | Where-Object { [Environment]::GetEnvironmentVariable($name, $_) }
    })
    $pre = $result.preflight
    $pre.services = @(Get-CimInstance Win32_Service -Filter "Name LIKE '%Ollama%' OR DisplayName LIKE '%Ollama%'" -Property Name).Count
    $pre.processes = @(Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue).Count
    $pre.binaryOnPath = [bool](Get-Command ollama.exe -CommandType Application -ErrorAction SilentlyContinue)
    $pre.knownBinaryPaths = @($binaryPaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }).Count
    $pre.uninstallEntries = @(Get-OllamaEntries).Count
    $pre.modelFileCount = @(Get-ModelFiles).Count
    $pre.kioskRootExists = Test-Path -LiteralPath $kioskRoot
    $pre.kioskShortcutExists = Test-Path -LiteralPath $shortcut
    $pre.ollamaPortInUse = [bool]([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object Port -eq 11434)
    $pre.overrideNames = $overrides
    $pre.chromeInstalled = [bool]$chrome
    if ($chrome) { $pre.chromeVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($chrome).ProductVersion }
    $pre.installVolumeFreeBytes = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($kioskRoot)).AvailableFreeSpace
    $pre.modelVolumeFreeBytes = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($modelRoot)).AvailableFreeSpace
    $pre.tempVolumeFreeBytes = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($privateDir)).AvailableFreeSpace
    $pre.ramBytes = (Get-CimInstance Win32_ComputerSystem -Property TotalPhysicalMemory).TotalPhysicalMemory
    $pre.clean = !($pre.services -or $pre.processes -or $pre.binaryOnPath -or $pre.knownBinaryPaths -or $pre.uninstallEntries -or
        $pre.modelFileCount -or $pre.kioskRootExists -or $pre.kioskShortcutExists -or $pre.ollamaPortInUse -or $overrides.Count)
    if (!$pre.clean) { throw 'Existing installation, listener, model data or override detected.' }
    if (!$pre.chromeInstalled) { throw 'Normal Google Chrome is missing.' }
    if ($pre.modelVolumeFreeBytes -lt 7GB -or $pre.installVolumeFreeBytes -lt 1GB -or $pre.tempVolumeFreeBytes -lt 1GB) {
        throw 'Insufficient available disk capacity for the initial download.'
    }
    Mark-Time preflightPassed
    Save-Evidence

    $result.stage = 'release-download'
    Fetch-Release
    $result.setup.bytes = (Get-Item -LiteralPath $setupFile).Length
    $result.setup.sha256Actual = (Get-FileHash -LiteralPath $setupFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($result.setup.sha256Actual -cne $releaseHash) { throw 'Release checksum mismatch.' }
    Mark-Time releaseVerified

    $result.stage = 'normal-installer'
    # Persistent native file redirection keeps the app's output writable in later workflow steps.
    @('@echo off','"%~dp0KioskAI-Setup.exe" <NUL >"%~dp0setup.stdout" 2>"%~dp0setup.stderr"','exit /b %errorlevel%') |
        Set-Content -LiteralPath (Join-Path $privateDir 'launch.cmd') -Encoding ascii
    $launcher = Start-Process -FilePath $env:ComSpec -ArgumentList '/d /c launch.cmd' -WorkingDirectory $privateDir -WindowStyle Hidden -PassThru
    Mark-Time normalExeStarted
    $lastReport = -30
    $lastLength = -1L
    $lastSignatureTry = -50
    $after = $result.after
    $after.ollamaInstallerPublisherVerified = $false
    $after.normalChatReady = $false
    while ($timer.Elapsed.TotalMinutes -lt $WaitMinutes) {
        $seconds = [int]$timer.Elapsed.TotalSeconds
        $stdout = Read-SharedText $outFile
        if ($stdout -match '공식 배포처에서 Ollama') { Mark-Time officialInstallerRequested }
        if ($stdout -match 'qwen3\.5:9b 모델을 내려받습니다') { Mark-Time freshPullRequested }
        $progressLines = [regex]::Matches($stdout, '(?m)^pulling [0-9a-f]{8,64}\s+(\d{1,3})%\s*$')
        if ($progressLines.Count) { $after.latestLayerPercent = [int]$progressLines[-1].Groups[1].Value }
        if ($stdout -match '(?m)^success\s*$') { Mark-Time pullSucceeded }
        if ($stdout -match '로컬 모델과 이미지 입력 기능을 확인했습니다') { Mark-Time bootstrapVerifiedVision }
        if ($stdout -match '첫 주문을 위해 모델을 메모리에 준비합니다') { Mark-Time normalWarmupStarted }
        if (!$after.ollamaInstallerPublisherVerified) {
            $tempInstall = Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'kioskai-ollama-*' |
                ForEach-Object { Join-Path $_.FullName 'OllamaSetup.exe' } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
            if ($tempInstall) {
                $length = (Get-Item -LiteralPath $tempInstall).Length
                if ($length -gt 1MB -and $length -eq $lastLength -and $seconds -ge $lastSignatureTry + 10) {
                    $lastSignatureTry = $seconds
                    $after.ollamaInstallerPublisherVerified = Get-Publisher $tempInstall
                    if ($after.ollamaInstallerPublisherVerified) { Mark-Time signedOllamaInstallerObserved }
                }
                $lastLength = $length
            }
        }
        if (!$after.ollamaVersion) {
            try { $version = Invoke-RestMethod "$ollamaUrl/api/version" -NoProxy -TimeoutSec 2; if ($version.version -match '^\d+[.\dA-Za-z+-]*$') { $after.ollamaVersion = $version.version; Mark-Time defaultOllamaPortReady } } catch {}
        }
        $session = (Read-SharedText (Join-Path $kioskRoot 'session') 1).Trim()
        if ($session -match '^\d{4,5}$' -and [int]$session -le 65535 -and [int]$session -ge 1024) {
            $uiUrl = 'http://127.0.0.1:' + $session
            try {
                $page = (Invoke-WebRequest "$uiUrl/" -NoProxy -TimeoutSec 5).Content
                if ($page -match '<title>KioskAI[^<]*</title>' -and $page -match '<script nonce="([0-9a-f]{64})">') {
                    $headers = @{'X-KioskAI-Token' = $Matches[1]}
                    $state = Invoke-RestMethod "$uiUrl/api/state" -Headers $headers -NoProxy -TimeoutSec 5
                    if ($state.ready -eq $true -and $state.model -eq $modelName -and $state.model_provider -eq 'ollama') {
                        $after.normalChatReady = $true
                        $after.chatPort = [int]$session
                        Mark-Time normalChatReady
                        break
                    }
                }
            } catch {}
        }
        if ($launcher.HasExited) { $result.setup.exitCode = $launcher.ExitCode; throw 'Normal installer exited before the chat was ready.' }
        if ($seconds -ge $lastReport + 30) {
            $lastReport = $seconds
            Write-Host ('[{0}s] normal setup running, default Ollama API ready={1}, latest layer progress={2}' -f $seconds, [bool]$after.ollamaVersion, $after.latestLayerPercent)
            Save-Evidence
        }
        Start-Sleep -Seconds 3
    }
    if (!$after.normalChatReady) { throw 'Timeout waiting for the normal installed chat.' }

    $result.stage = 'installed-runtime-and-model'
    $cli = Join-Path $ollamaRoot 'ollama.exe'
    $after.ollamaCliInstalled = Test-Path -LiteralPath $cli -PathType Leaf
    $after.ollamaCliPublisherVerified = [bool](Get-Publisher $cli)
    $after.ollamaAppPublisherVerified = [bool](Get-Publisher (Join-Path $ollamaRoot 'ollama app.exe'))
    $after.ollamaUninstallEntries = @(Get-OllamaEntries).Count
    $after.defaultEndpointPort = 11434
    $after.listenerIsInstalledOllama = $false
    $listeners = @(Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction Stop)
    foreach ($listener in $listeners) {
        $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($owner.Path -and $owner.Path.StartsWith(($ollamaRoot + '\'), [StringComparison]::OrdinalIgnoreCase)) {
            $after.listenerIsInstalledOllama = $true
        }
    }
    $models = @(Get-ModelFiles)
    $after.modelFileCount = $models.Count
    $after.modelCacheBytes = [int64](($models | Measure-Object -Property Length -Sum).Sum)
    $tag = (Invoke-RestMethod "$ollamaUrl/api/tags" -NoProxy -TimeoutSec 10).models | Where-Object name -eq $modelName | Select-Object -First 1
    $after.modelTag = $tag.name
    $after.modelBytes = $tag.size
    $after.modelDigest = if ($tag.digest -match '^[0-9a-f]{64}$') { $tag.digest } else { $null }
    $show = Invoke-RestMethod "$ollamaUrl/api/show" -NoProxy -Method Post -ContentType application/json -Body (@{model=$modelName} | ConvertTo-Json) -TimeoutSec 20
    $after.vision = 'vision' -in $show.capabilities
    $after.completion = 'completion' -in $show.capabilities
    if (!$after.ollamaCliInstalled -or !$after.listenerIsInstalledOllama -or !$after.ollamaVersion -or
        !($after.ollamaInstallerPublisherVerified -or $after.ollamaAppPublisherVerified -or $after.ollamaCliPublisherVerified)) {
        throw 'Could not verify newly installed Ollama, default listener and the official publisher.'
    }
    if (!$result.secondsFromStart.Contains('freshPullRequested') -or !$result.secondsFromStart.Contains('officialInstallerRequested') -or
        $after.modelTag -ne $modelName -or !$after.modelDigest -or $after.modelBytes -le 0 -or $after.modelCacheBytes -lt $after.modelBytes -or
        !$after.vision -or !$after.completion) { throw 'Fresh download and vision model proof did not pass.' }
    $versions = @(Get-ChildItem -LiteralPath (Join-Path $kioskRoot 'versions') -Directory | Where-Object Name -match '^[0-9a-f]{16}-[0-9a-f]{16}$')
    if ($versions.Count -ne 1 -or !(Test-Path -LiteralPath $shortcut)) { throw 'Expected one new app version and a Start menu shortcut.' }
    $app = Join-Path $versions[0].FullName 'app'
    $after.installedAppVerified = (Test-Path -LiteralPath (Join-Path $app 'desktop_app.py') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $versions[0].FullName 'python/python.exe') -PathType Leaf) -and
        (Get-FileHash -LiteralPath (Join-Path $versions[0].FullName 'KioskAI.exe')).Hash.ToLowerInvariant() -ceq $releaseHash
    if (!$after.installedAppVerified) { throw 'Installed launcher or bundled runtime did not match.' }
    Add-Content -LiteralPath $env:GITHUB_ENV -Encoding utf8NoBOM -Value @("KIOSKAI_TEST_APP=$app", "KIOSKAI_TEST_PORT=$session")
    $result.stage = 'ready-for-order'
    $result.passed = $true
    Mark-Time installChecksPassed
} catch {
    $result.errorType = $_.Exception.GetType().Name
    $knownReason = @('Existing installation, listener, model data or override detected.','Normal Google Chrome is missing.',
        'Insufficient available disk capacity for the initial download.','Release checksum mismatch.','Normal installer exited before the chat was ready.',
        'Timeout waiting for the normal installed chat.','Could not verify newly installed Ollama, default listener and the official publisher.',
        'Fresh download and vision model proof did not pass.','Expected one new app version and a Start menu shortcut.',
        'Installed launcher or bundled runtime did not match.','Unexpected GitHub download origin.','Too many release redirects.')
    if ($_.Exception.Message -in $knownReason) { $result.reason = $_.Exception.Message }
    $result.setup.stderr = Get-SafeFailure (Read-SharedText $errFile)
    $result.setup.stdoutDiagnostics = Get-SafeFailure (Read-SharedText $outFile)
    $result.setup.checkDiagnostics = Get-SafeFailure ($_.Exception.ToString())
    if ($result.secondsFromStart.Contains('normalExeStarted')) {
        # Preserve evidence of stages already completed when a later stage (such as warmup) fails.
        try {
            $result.after.ollamaCliInstalled = Test-Path -LiteralPath (Join-Path $ollamaRoot 'ollama.exe') -PathType Leaf
            $partialModels = @(Get-ModelFiles)
            $result.after.modelFileCount = $partialModels.Count
            $result.after.modelCacheBytes = [int64](($partialModels | Measure-Object -Property Length -Sum).Sum)
        } catch {}
        if ($result.after.ollamaVersion) {
            try {
                $installedTag = (Invoke-RestMethod "$ollamaUrl/api/tags" -NoProxy -TimeoutSec 3).models |
                    Where-Object name -eq $modelName | Select-Object -First 1
                if ($installedTag) {
                    $result.after.modelTag = $installedTag.name
                    $result.after.modelBytes = $installedTag.size
                    $modelInfo = Invoke-RestMethod "$ollamaUrl/api/show" -NoProxy -Method Post -ContentType application/json -Body (@{model=$modelName} | ConvertTo-Json) -TimeoutSec 7
                    $result.after.vision = 'vision' -in $modelInfo.capabilities
                    $result.after.completion = 'completion' -in $modelInfo.capabilities
                }
            } catch {}
        }
    }
    Write-Host ('Verification stopped during {0}. See install.json.' -f $result.stage)
} finally {
    $result.utcFinished = [DateTime]::UtcNow.ToString('o')
    Save-Evidence
}
if (!$result.passed) { exit 1 }
