$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
$name = if ($env:POTIONUI_KIT) { $env:POTIONUI_KIT } else { "Mara" }

$kit = (Invoke-RestMethod -Method Post -Uri "$base/api/kits" -Headers $headers -ContentType "application/json" -Body (@{ name = $name; label = "Actor" } | ConvertTo-Json)).data.kit

$item = @{ name = "bio"; text = "Mara is a sailor with a red scarf." } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/api/kits/$($kit.id)/items" -Headers $headers -ContentType "application/json" -Body $item | Out-Null

$resolve = @{ text = "A portrait of @$($kit.path).bio at sea" } | ConvertTo-Json
(Invoke-RestMethod -Method Post -Uri "$base/api/kits/resolve" -Headers $headers -ContentType "application/json" -Body $resolve).data.expanded
