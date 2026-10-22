$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root
$required = @('tiny_v3_config.json','configuracao_privada/app_config.json','configuracao_privada/edicao_publicador.json','configuracao_privada/edicao_leitor.json')
foreach ($file in $required) {
    if (!(Test-Path -LiteralPath (Join-Path $root $file))) { throw "Configure primeiro o arquivo privado: $file" }
}
$archive = Join-Path $root 'HL_Conferencia_NFe/assets/SumatraPDF-3.6.1-64.zip'
if (!(Test-Path -LiteralPath $archive)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $archive -Parent) | Out-Null
    Invoke-WebRequest -Uri 'https://www.sumatrapdfreader.org/dl/rel/3.6.1/SumatraPDF-3.6.1-64.zip' -OutFile $archive
}
python tools/prepare_ui.py
if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar a interface.' }
python -m PyInstaller --noconfirm HL_Coordenadas_V7.spec
if ($LASTEXITCODE -ne 0) { throw 'Falha na compilacao.' }
