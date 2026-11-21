[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows') {
    throw 'Exact model preparation requires a disposable GitHub-hosted Windows guest.'
}

$expected = '6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'
$source = Join-Path $PSScriptRoot 'qwen3.5-9b.manifest.json'
$modelRoot = Join-Path $env:USERPROFILE '.ollama/models'
$evidenceDir = if ($env:EVIDENCE_DIR) { $env:EVIDENCE_DIR } else { Join-Path $env:RUNNER_TEMP 'kiosk-evidence' }
$null = New-Item -ItemType Directory -Force -Path $evidenceDir
$timer = [Diagnostics.Stopwatch]::StartNew()
$evidence = [ordered]@{
    tag = 'qwen3.5:9b'; expectedManifest = $expected; passed = $false; blobs = @(); stage = 'empty-cache'
}

function Receive-OfficialBlob($Client, [string]$Digest, [long]$Size, [string]$Path) {
    $uri = [uri]("https://registry.ollama.ai/v2/library/qwen3.5/blobs/sha256:" + $Digest)
    $cancel = [Threading.CancellationTokenSource]::new([TimeSpan]::FromMinutes(20))
    try {
        for ($n = 0; $n -lt 5; $n++) {
            if ($uri.Scheme -ne 'https' -or !$uri.IsDefaultPort -or $uri.UserInfo -or
                ($uri.Host -ne 'registry.ollama.ai' -and !$uri.Host.EndsWith('.r2.cloudflarestorage.com'))) {
                throw 'Unexpected blob redirect.'
            }
            $response = $Client.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead, $cancel.Token).GetAwaiter().GetResult()
            try {
                if ([int]$response.StatusCode -in @(301,302,303,307,308)) {
                    $uri = [uri]::new($uri, $response.Headers.Location)
                    continue
                }
                if ([int]$response.StatusCode -ne 200) { throw "Blob HTTP status $([int]$response.StatusCode)." }
                $declared = $response.Content.Headers.ContentLength
                if ($null -ne $declared -and $declared -ne $Size) { throw 'Unexpected blob content length.' }
                $stream = $response.Content.ReadAsStream()
                $output = [IO.File]::Open($Path, 'Create', 'Write', 'None')
                try {
                    $buffer = [byte[]]::new(1MB)
                    while (($bytes = $stream.ReadAsync($buffer, 0, $buffer.Length, $cancel.Token).GetAwaiter().GetResult()) -gt 0) {
                        if ($output.Position + $bytes -gt $Size) { throw 'Blob exceeded expected size.' }
                        $output.Write($buffer, 0, $bytes)
                    }
                    if ($output.Position -ne $Size) { throw 'Incomplete blob.' }
                } finally { $output.Dispose() }
                return
            } finally { $response.Dispose() }
        }
        throw 'Too many registry redirects.'
    } finally { $cancel.Dispose() }
}

$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [Net.Http.HttpClient]::new($handler)
try {
    if ($env:OLLAMA_MODELS) { throw 'Custom model store detected.' }
    if ((Test-Path -LiteralPath $modelRoot) -and (Get-ChildItem -LiteralPath $modelRoot -Recurse -File -Force | Select-Object -First 1)) {
        throw 'Existing model cache files detected.'
    }
    $evidence.emptyCache = $true
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()
    if ($actual -cne $expected) { throw 'Original manifest digest mismatch.' }
    $manifest = Get-Content -Raw -LiteralPath $source | ConvertFrom-Json
    $blobDir = Join-Path $modelRoot 'blobs'
    $null = New-Item -ItemType Directory -Force -Path $blobDir
    foreach ($item in @($manifest.config) + $manifest.layers) {
        if ($item.digest -cnotmatch '^sha256:([a-f0-9]{64})$' -or $item.size -le 0) { throw 'Invalid model manifest entry.' }
        $digest = $item.digest.Substring(7)
        $evidence.stage = 'blob-' + $digest.Substring(0,12)
        $dest = Join-Path $blobDir ('sha256-' + $digest)
        $partial = $dest + '.partial'
        for ($attempt = 1; $attempt -le 2; $attempt++) {
            try {
                Receive-OfficialBlob $client $digest $item.size $partial
                $fileHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $partial).Hash.ToLowerInvariant()
                if ($fileHash -cne $digest) { throw 'Blob SHA-256 mismatch.' }
                break
            } catch {
                if ($attempt -ge 2) { throw }
                Write-Host "Retrying $($evidence.stage) from the official registry."
            }
        }
        [IO.File]::Move($partial, $dest)
        $evidence.blobs += [ordered]@{ type=$item.mediaType; bytes=[long]$item.size; sha256=$fileHash }
        Write-Host "Verified $($item.mediaType) ($($item.size) bytes)."
    }
    $evidence.stage = 'manifest'
    $tagDir = Join-Path $modelRoot 'manifests/registry.ollama.ai/library/qwen3.5'
    $null = New-Item -ItemType Directory -Force -Path $tagDir
    $dest = Join-Path $tagDir '9b'
    [IO.File]::Copy($source, $dest)
    $evidence.installedManifest = (Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash.ToLowerInvariant()
    if ($evidence.installedManifest -cne $expected) { throw 'Installed manifest checksum mismatch.' }
    $evidence.passed = $true
} catch {
    $evidence.errorType = $_.Exception.GetType().Name
    throw "Exact model verification failed during $($evidence.stage)."
} finally {
    $client.Dispose()
    $evidence.seconds = [int]$timer.Elapsed.TotalSeconds
    $evidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $evidenceDir 'exact-model.json') -Encoding utf8NoBOM
}
