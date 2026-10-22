from pathlib import Path

root = Path(SPECPATH)

# Nenhuma edicao leva access/refresh tokens de outro PC. O publicador autoriza
# o usuario no destino; o leitor consulta o Supabase sem login no Tiny.
a = Analysis(
    [str(root / 'unified_v7.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / 'HL_COORDENADAS_v7.html'), '.'),
        (str(root / 'ui'), 'ui'),
        (str(root / 'tiny_v3_config.json'), '.'),
        (str(root / 'HL_Conferencia_NFe' / 'static'), 'conference_static'),
        (str(root / 'HL_Conferencia_NFe' / 'tiny_label_model.json'), '.'),
        (str(root / 'HL_Conferencia_NFe' / 'THIRD_PARTY_NOTICES.txt'), '.'),
        (str(root / 'HL_Conferencia_NFe' / 'assets' / 'SumatraPDF-3.6.1-64.zip'), '.'),
    ],
    hiddenimports=['win32print', 'win32timezone'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc'],
    noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)

for edition, name in [('publicador','HL_Coordenadas_Publicador'), ('leitor','HL_Coordenadas_Leitor')]:
    profile = [('hl_edicao.json', str(root / 'configuracao_privada' / ('edicao_' + edition + '.json')), 'DATA'),
               ('hl_public_config.json', str(root / 'configuracao_privada' / 'app_config.json'), 'DATA')]
    EXE(
        pyz, a.scripts, a.binaries, a.datas + profile, [], name=name+'_V7',
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
        console=True, disable_windowed_traceback=False, argv_emulation=False,
        target_arch=None, codesign_identity=None, entitlements_file=None,
        version=str(root / 'version_v7.txt'),
    )
