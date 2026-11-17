$ErrorActionPreference = 'Stop'
$Destination = 'C:\yolo\camera-inference-obb'
$Archive = Join-Path $env:TEMP 'obb-inference-update.zip'
Invoke-WebRequest -UseBasicParsing 'http://100.116.119.120:8080/obb-inference-update.zip' -OutFile $Archive
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
Write-Host 'Starting trained YOLO26l-OBB on port 8090. Keep this window open.'
& (Join-Path $Destination 'start-obb.ps1')
