$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }

$presets = Invoke-RestMethod -Uri "$base/api/presets" -Headers $headers
foreach ($preset in $presets.data) {
    Write-Output "$($preset.id)  $($preset.name)  $($preset.modes -join ',')"
}
