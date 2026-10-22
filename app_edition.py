"""Perfil incluido no executavel; o leitor nunca habilita publicacao por arquivo local."""
import json


def load_edition(bundle):
    path = bundle / 'hl_edicao.json'
    if not path.is_file():
        return {'edicao':'padrao'}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('edicao') in ('leitor','publicador'):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {'edicao':'leitor'}
