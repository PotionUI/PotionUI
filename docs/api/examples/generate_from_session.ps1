$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
$session = $env:POTIONUI_SESSION

$template = Invoke-RestMethod -Uri "$base/api/sessions/$session/request" -Headers $headers
foreach ($item in $template.data.fields) {
    Write-Output "$($item.name)  $($item.type)  $($item.value)"
}

$body = '{"overrides": {"prompt": "a glass potion bottle on a shelf", "count": 1}}'
$started = Invoke-RestMethod -Method Post -Uri "$base/api/sessions/$session/generate" -Headers $headers -ContentType "application/json" -Body $body
$id = $started.data.generation_id

do {
    Start-Sleep -Seconds 1
    $state = (Invoke-RestMethod -Uri "$base/api/generations/$id/status" -Headers $headers).data
} while ($state.status -in @("pending", "running"))

if ($state.status -ne "completed") { throw "generation $($state.status)" }

$generation = (Invoke-RestMethod -Uri "$base/api/generations/history/$id" -Headers $headers).data
foreach ($file in $generation.files) {
    $name = "$id-$($file.id).$($file.mime_type.Split('/')[1])"
    Invoke-WebRequest -Uri "$base/api/media/files/$($file.id)" -Headers $headers -OutFile $name
    Write-Output "saved $name"
}
