$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
New-Item -ItemType Directory -Force -Path history | Out-Null

$offset = 0
do {
    $page = (Invoke-RestMethod -Uri "$base/api/generations/history?status=completed&limit=20&offset=$offset" -Headers $headers).data
    foreach ($generation in $page.generations) {
        foreach ($file in $generation.files) {
            $target = "history/$($generation.id)-$($file.id).$($file.mime_type.Split('/')[1])"
            if (-not (Test-Path $target)) {
                Invoke-WebRequest -Uri "$base/api/media/files/$($file.id)" -Headers $headers -OutFile $target
                Write-Output "saved $target"
            }
        }
    }
    $offset += $page.limit
} while ($offset -lt $page.total)
