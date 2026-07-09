Set-Location "C:\Users\Gautam\OneDrive\Desktop\AI News Dashboard Code"
$existing = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like '*live_worker.py*' }
if (-not $existing) {
    Start-Process python -ArgumentList "live_worker.py --ocr" -WindowStyle Hidden `
        -RedirectStandardOutput worker.log -RedirectStandardError worker.err.log
}
