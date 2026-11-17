$ErrorActionPreference = 'Stop'
$reportRoot = Split-Path $PSScriptRoot -Parent
$reportPath = Join-Path $reportRoot '5조 고무줄 총 자동화 시스템 개발 보고서.docx'
$previewDir = Join-Path $PSScriptRoot 'word_preview'
if (-not (Test-Path -LiteralPath $previewDir)) { New-Item -ItemType Directory -Path $previewDir | Out-Null }
$reportPdf = Join-Path $previewDir 'report-native-word.pdf'
$reportWord = $null
$reportDocument = $null
try {
    $reportWord = New-Object -ComObject Word.Application
    $reportWord.Visible = $false
    $reportWord.DisplayAlerts = 0
    $reportWord.AutomationSecurity = 3
    $reportDocument = $reportWord.Documents.Open($reportPath, $false, $true)
    $reportDocument.Repaginate()
    $reportImagePages = @()
    foreach ($reportShape in $reportDocument.InlineShapes) {
        $reportImagePages += [int]$reportShape.Range.Information(3)
    }
    $reportPageCount = [int]$reportDocument.ComputeStatistics(2)
    $reportDocument.ExportAsFixedFormat($reportPdf, 17)
    $reportPagination = [ordered]@{
        renderer = 'Microsoft Word native PDF export'
        pages = $reportPageCount
        figurePages = $reportImagePages
        firstHardwareImageOnPageOne = ($reportImagePages[0] -eq 1)
        pdf = $reportPdf
    }
    $reportPagination | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $previewDir 'native-pagination.json') -Encoding UTF8
    $reportPagination | ConvertTo-Json -Depth 5
    if ($reportImagePages[0] -ne 1) { throw 'The first hardware image is not on the first page.' }
} finally {
    if ($null -ne $reportDocument) { $reportDocument.Close(0); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($reportDocument) }
    if ($null -ne $reportWord) { $reportWord.Quit(); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($reportWord) }
}
