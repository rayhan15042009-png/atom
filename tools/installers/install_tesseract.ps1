# Install Tesseract OCR on Windows
$url = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.0/tesseract-ocr-w64-setup-v5.3.0.exe"
$installer = "C:\Temp\tesseract-setup.exe"
$installPath = "C:\Program Files\Tesseract-OCR"

# Create temp directory if needed
if (-not (Test-Path "C:\Temp")) {
    New-Item -ItemType Directory -Path "C:\Temp" -Force | Out-Null
}

# Enable TLS 1.2 for downloads
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12

Write-Host "Downloading Tesseract OCR v5.3.0..."
try {
    (New-Object System.Net.WebClient).DownloadFile($url, $installer)
    Write-Host "Downloaded successfully to $installer"
} catch {
    Write-Host "Download failed: $_"
    exit 1
}

Write-Host "Installing Tesseract to $installPath..."
try {
    Start-Process -FilePath $installer -ArgumentList "/S /D=$installPath" -Wait -NoNewWindow
    Write-Host "Tesseract installed successfully!"
} catch {
    Write-Host "Installation failed: $_"
    exit 1
}

# Add to PATH if not already there
$envPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($envPath -notlike "*Tesseract*") {
    Write-Host "Adding Tesseract to system PATH..."
    [Environment]::SetEnvironmentVariable("Path", "$envPath;$installPath", "Machine")
    Write-Host "PATH updated. Please restart your terminal for changes to take effect."
}

# Verify installation
Write-Host "Verifying installation..."
if (Test-Path "$installPath\tesseract.exe") {
    Write-Host "✓ Tesseract.exe found at $installPath"
    & "$installPath\tesseract.exe" --version
    Write-Host "✓ Tesseract installation verified!"
} else {
    Write-Host "✗ Tesseract.exe not found at $installPath"
    exit 1
}

Write-Host "Installation complete!"
