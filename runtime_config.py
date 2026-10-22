"""Configuracao local/empacotada. Nenhuma credencial pertence ao repositorio."""
import json
import os


def load_public_config(bundle):
    candidates = [bundle / 'hl_public_config.json', bundle / 'configuracao_privada' / 'app_config.json']
    data = {}
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding='utf-8'))
            break
    return {
        'supabase_url': os.getenv('HL_SUPABASE_URL') or data.get('supabase_url', ''),
        'supabase_key': os.getenv('HL_SUPABASE_KEY') or data.get('supabase_key', ''),
    }
