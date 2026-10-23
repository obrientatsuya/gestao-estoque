"""Valida conteudo dos pacotes sem exibir valores privados."""
import json
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parents[1]


def main():
    for edition,label in [('publicador','Publicador'),('leitor','Leitor')]:
        path=ROOT/'dist'/f'HL_Coordenadas_{label}_V7.exe'
        archive=CArchiveReader(str(path))
        names=set(archive.toc)
        assert not any('tiny_v3_tokens' in name.lower() for name in names),'Token empacotado'
        profile=json.loads(archive.extract('hl_edicao.json'))
        assert profile['edicao']==edition,'Perfil incorreto'
        assert bool(profile.get('chave_publicador'))==(edition=='publicador'),'Segregacao de chave incorreta'
        public=json.loads(archive.extract('hl_public_config.json'))
        assert public.get('supabase_url') and public.get('supabase_key'),'Configuracao publica ausente'
        for asset in ['HL_COORDENADAS_v7.html','ui\\v7.css','ui\\v7.js','ui\\warehouse-metrics.js','conference_static\\ui\\conference-v7.css']:
            assert asset in names,f'Asset ausente: {asset}'
        assert b'motionToggle' in archive.extract('HL_COORDENADAS_v7.html'),'Preferencia de animacoes ausente'
        assert b'v7.2' in archive.extract('HL_COORDENADAS_v7.html'),'Interface desatualizada'
        if edition=='leitor':
            private=json.loads((ROOT/'configuracao_privada/edicao_publicador.json').read_text(encoding='utf-8'))
            key=private['chave_publicador'].encode()
            assert all(key not in archive.extract(name) for name in names if name.endswith(('.json','.html','.js','.py'))),'Chave de publicacao no Leitor'
        print(f'{label}: perfil e assets OK, sem tokens; {path.stat().st_size/1024/1024:.1f} MiB')


if __name__=='__main__':main()
