"""Audita o indice Git sem exibir valores de credenciais."""
import argparse
import json
from pathlib import Path
import re
import subprocess

BLOCKED = {'.exe','.dll','.zip','.rar','.7z','.db','.sqlite','.sqlite3','.pdf','.pem','.key','.pfx','.p12','.pyc'}
SECRET_NAMES = {'access_token','refresh_token','client_secret','client_id','supabase_key','supabase_url','chave_publicador','chave','token'}
PATTERNS = [
    re.compile(rb'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}'),
    re.compile(rb'(?:ghp_|github_pat_|sb_secret_)[A-Za-z0-9_]{20,}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'[a-z]{20}\.supabase\.co'),
]


def local_secrets(root):
    found = set()
    paths = list((root/'configuracao_privada').glob('*.json'))
    paths += [root/'tiny_v3_config.json',root/'tiny_v3_tokens.json']
    def visit(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key.lower() in SECRET_NAMES and isinstance(child,str) and len(child)>12:
                    found.add(child.encode())
                visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    for path in paths:
        if path.is_file():visit(json.loads(path.read_text(encoding='utf-8')))
    return found


def check(repo, private_root):
    secrets = local_secrets(private_root)
    names = subprocess.check_output(['git','-C',str(repo),'diff','--cached','--name-only','--diff-filter=ACM','-z']).split(b'\0')
    errors=[]
    for raw_name in filter(None,names):
        name=raw_name.decode('utf-8')
        path=Path(name)
        lower=name.lower()
        example=lower.endswith('.example.json')
        forbidden = path.suffix.lower() in BLOCKED or any(part in {'configuracao_privada','data','dist','build','__pycache__','tmp'} for part in path.parts)
        forbidden |= not example and ('tokens' in lower or lower.endswith(('tiny_v3_config.json','app_config.json','hl_edicao.json','vendas_publicador.json')))
        if forbidden:
            errors.append(name+' (arquivo privado/artefato)');continue
        content=subprocess.check_output(['git','-C',str(repo),'show',':'+name])
        if any(secret in content for secret in secrets) or any(pattern.search(content) for pattern in PATTERNS):
            errors.append(name+' (possivel credencial)')
    if errors:
        raise SystemExit('PUBLICACAO BLOQUEADA:\n'+'\n'.join(errors))
    print(f'Indice aprovado: {len(list(filter(None,names)))} arquivo(s), sem credenciais detectadas ou binarios proibidos.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--private-root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    check(args.repo.resolve(),args.private_root.resolve())
