$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$destination = Join-Path $root 'repositorio_v7'
if (!(Test-Path -LiteralPath (Join-Path $destination '.git'))) { throw 'Clone o repositorio em repositorio_v7 primeiro.' }
$files = @(
    '.gitignore','README.md','requirements.txt','INICIAR_V7.bat',
    'app_edition.py','runtime_config.py','box_insights.py','box_occupancy.py','shared_sales.py','sales_diagnostic.py','warehouse_controls.py','supabase_bloqueio_caixas.sql',
    'calcular_saidas_tiny.py','enviar_localizacoes_tiny_v3.py','tiny_v3_auth.py',
    'sync_erp_lote.py','unified_v7.py','HL_COORDENADAS_v7.html','HL_Coordenadas_V7.spec','version_v7.txt',
    'supabase_migracao.sql','supabase_resumo_vendas.sql','supabase_chave_publicador.sql','supabase_ocupacao_unidades.sql','MIGRACAO_SEM_ANDAR.sql',
    'CONFIGURAR_RESUMO_VENDAS.md','COMO_USAR_AS_DUAS_EDICOES.md',
    'HL_Conferencia_NFe/__init__.py','HL_Conferencia_NFe/app.py','HL_Conferencia_NFe/pdf_parser.py',
    'HL_Conferencia_NFe/tiny_client.py','HL_Conferencia_NFe/tiny_print.py',
    'HL_Conferencia_NFe/tiny_label_model.json','HL_Conferencia_NFe/THIRD_PARTY_NOTICES.txt',
    'HL_Conferencia_NFe/requirements.txt','HL_Conferencia_NFe/static/index.html',
    'HL_Conferencia_NFe/static/app.js','HL_Conferencia_NFe/static/style.css',
    'tools/prepare_ui.py','tools/demo_server.py','tools/security_check.py','tools/build_v7.ps1',
    'tools/check_build.py','tools/smoke_build.py','tools/smoke_replace_instance.py','tools/export_source.ps1','tests/test_count_queue.cjs','tests/test_warehouse_metrics.cjs'
)
$files += Get-ChildItem -LiteralPath (Join-Path $root 'tests') -Filter 'test_*.py' | ForEach-Object { 'tests/' + $_.Name }
foreach ($file in $files) {
    $target = Join-Path $destination $file
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item -LiteralPath (Join-Path $root $file) -Destination $target -Force
}
foreach ($directory in @('ui','config','docs')) {
    Copy-Item -LiteralPath (Join-Path $root $directory) -Destination $destination -Recurse -Force
}
Write-Output 'Fontes e documentacao exportados. Nenhuma pasta privada ou executavel foi copiado.'
