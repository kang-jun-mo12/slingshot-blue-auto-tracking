param(
    [string]$Model = 'C:\yolo\yolo26l_obb_dataset_2856\runs\yolo26l_obb_1920x1088\weights\best.pt',
    [switch]$Cpu
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Model -PathType Leaf)) {
    throw "Trained checkpoint not found: $Model. Pass -Model with the trained best.pt path."
}
$Checkpoint = Get-Item -LiteralPath $Model
Write-Host ('Selected trained checkpoint: ' + $Checkpoint.FullName)
Write-Host ('Modified: ' + $Checkpoint.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
Write-Host ('SHA256: ' + (Get-FileHash -LiteralPath $Model -Algorithm SHA256).Hash)
# The exact checkpoint is selected; no pretrained weights are downloaded instead.
& (Join-Path $PSScriptRoot 'start-desktop.ps1') -Model $Model -ImageSize 1920 -ExpectedTask obb -RequireBlueTarget -Cpu:$Cpu
