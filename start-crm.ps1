$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $env:CRM_API_TOKEN) {
    $randomBytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $random.GetBytes($randomBytes)
    $random.Dispose()
    $env:CRM_API_TOKEN = [Convert]::ToBase64String($randomBytes)
}
Write-Host 'Byte Studio — локальный сайт и CRM'
Write-Host 'Адреса сайта и CRM появятся ниже после запуска сервера.'
Write-Host 'Используется свободный локальный порт.'
Write-Host 'Ключ для входа (не передавай другим):'
Write-Host $env:CRM_API_TOKEN
Write-Host 'Для остановки нажми Ctrl+C. После нового запуска ключ может измениться.'
$env:PYTHONDONTWRITEBYTECODE = '1'
python crm_api.py --port 0
