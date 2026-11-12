$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
$form = if ($env:POTIONUI_FORM) { $env:POTIONUI_FORM | ConvertFrom-Json } else { [pscustomobject]@{} }
$field = if ($env:POTIONUI_INPUT_FIELD) { $env:POTIONUI_INPUT_FIELD } else { "references" }
$mode = if ($env:POTIONUI_MODE) { $env:POTIONUI_MODE } else { "edit" }

$uploaded = curl.exe -sS --fail-with-body -X POST "$base/api/media/upload" -H "Authorization: Bearer $($env:POTIONUI_KEY)" -F "file=@$($args[0])" | ConvertFrom-Json
$form | Add-Member -NotePropertyName $field -NotePropertyValue @($uploaded.data.relative_path) -Force
$formJson = $form | ConvertTo-Json -Depth 10 -Compress

$body = @"
{"preset_id": "$($env:POTIONUI_PRESET)", "mode": "$mode", "prompt": "make the liquid deep blue", "form_data": $formJson}
"@
$started = Invoke-RestMethod -Method Post -Uri "$base/api/generations/start" -Headers $headers -ContentType "application/json" -Body $body
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
