$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$pdfType = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime]
$fileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$streamType = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$optionsType = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType=WindowsRuntime]
$operationMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$actionMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
function Wait-PdfOperation($operation, $resultType) {
    $task = $operationMethod.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$previewDir = Join-Path (Get-Location) 'document_work/word_preview'
$pdfPath = Join-Path $previewDir 'report-native-word.pdf'
$file = Wait-PdfOperation ($fileType::GetFileFromPathAsync($pdfPath)) $fileType
$pdf = Wait-PdfOperation ($pdfType::LoadFromFileAsync($file)) $pdfType
for ($index = 0; $index -lt $pdf.PageCount; $index++) {
    $page = $pdf.GetPage($index)
    $memory = [Activator]::CreateInstance($streamType)
    try {
        $options = [Activator]::CreateInstance($optionsType)
        $options.DestinationWidth = 1240
        $task = $actionMethod.Invoke($null, @($page.RenderToStreamAsync($memory, $options)))
        $task.GetAwaiter().GetResult()
        $memory.Seek(0)
        $input = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($memory)
        $destination = Join-Path $previewDir ('page-{0:D2}.png' -f ($index + 1))
        $output = [System.IO.File]::Create($destination)
        try { $input.CopyTo($output) } finally { $output.Dispose(); $input.Dispose() }
        Write-Output $destination
    } finally { $memory.Dispose(); $page.Dispose() }
}
