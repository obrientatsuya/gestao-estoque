"""Conta localmente saidas com valor positivo; Tiny acessado somente por GET."""
import argparse
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

from box_insights import BoxInsights
from enviar_localizacoes_tiny_v3 import TinyV3Client


def movement_rows(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ('itens', 'movimentacoes', 'logs'):
            if isinstance(body.get(key), list):
                return body[key]
    raise ValueError('JSON sem lista de movimentos (itens, movimentacoes ou logs).')


def calculate(rows):
    sales = {}
    ignored = 0
    for index, item in enumerate(rows, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Movimento {index} nao e um objeto JSON.')
        parsed = BoxInsights.parse_movement(item)
        if parsed is None:
            ignored += 1
            continue
        mid, when, quantity, value = parsed
        record = {'movimento':mid, 'data':when, 'quantidade':quantity, 'valor':value}
        if mid in sales and sales[mid] != record:
            raise ValueError(f'Movimento {mid} repetido com dados diferentes; contagem nao confirmada.')
        sales[mid] = record
    units = sum((Decimal(str(r['quantidade'])) for r in sales.values()), Decimal(0))
    return {'registros_lidos':len(rows), 'registros_ignorados':ignored,
            'vendas':len(sales), 'unidades_vendidas':float(units),
            'regra':'tipo S, valor > 0 e quantidade diferente de zero',
            'saidas':sorted(sales.values(), key=lambda r:(r['data'],r['movimento']), reverse=True)}


def fetch_movements(client, product_id, start, end):
    offset = 0
    movements = []
    seen_pages = set()
    while True:
        # O tipo e o valor sao avaliados localmente, nao enviados como filtros.
        query = urlencode({'dataInicio':start, 'dataFim':end, 'limit':100, 'offset':offset})
        body = client.request_json('GET', f'/estoque/{product_id}/logs-movimentacao?'+query)['body']
        rows = movement_rows(body)
        pagination = (body.get('paginacao') or body) if isinstance(body, dict) else {}
        total = pagination.get('total')
        if not rows:
            if total is not None and offset < int(total):
                raise ValueError('API retornou pagina vazia antes de concluir o historico.')
            return movements
        signature = json.dumps(rows, sort_keys=True, ensure_ascii=False)
        if signature in seen_pages:
            raise ValueError('API repetiu uma pagina; contagem incompleta.')
        seen_pages.add(signature)
        movements.extend(rows)
        offset += len(rows)
        if total is not None and offset >= int(total):
            return movements
        limit = int(pagination.get('limit') or 100)
        if total is None and len(rows) < limit:
            return movements


def read_file(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(data, dict) and 'status' in data:
        if int(data['status']) != 200:
            raise ValueError(f"Arquivo contem resposta HTTP {data['status']}, nao movimentos.")
        data = data.get('body')
    rows = movement_rows(data)
    pagination = (data.get('paginacao') or data) if isinstance(data, dict) else {}
    total = pagination.get('total')
    if int(pagination.get('offset') or 0) != 0 or (total is not None and int(total) > len(rows)):
        raise ValueError('Arquivo contem apenas uma parte das paginas; forneca a lista completa.')
    return rows


def client_from_files(token_file=None, config_file=None):
    if token_file is None:
        folders = [Path(__file__).resolve().parent, Path.home()/'AppData/Local/HL_Coordenadas_V5']
        candidates = [p/'tiny_v3_tokens.json' for p in folders if (p/'tiny_v3_tokens.json').is_file()]
        if not candidates:
            raise ValueError('Token Tiny nao encontrado. Informe --tokens com o arquivo existente.')
        token_file = max(candidates, key=lambda p:p.stat().st_mtime)
    return TinyV3Client(token_file, 30, config_file or token_file.with_name('tiny_v3_config.json'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--arquivo', type=Path, help='JSON com a lista completa de movimentos')
    source.add_argument('--sku', help='SKU exato para consultar na API oficial')
    source.add_argument('--produto-id', type=int, help='ID interno do produto no Tiny')
    parser.add_argument('--inicio', default='2000-01-01', type=date.fromisoformat)
    parser.add_argument('--fim', default=date.today().isoformat(), type=date.fromisoformat)
    parser.add_argument('--tokens', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--saida', type=Path, help='Caminho do relatorio JSON')
    args = parser.parse_args(argv)
    try:
        if args.inicio > args.fim:
            raise ValueError('Data inicial posterior a data final.')
        if args.arquivo:
            rows = read_file(args.arquivo)
            origin = {'origem':'arquivo', 'arquivo':str(args.arquivo),
                      'cobertura':'registros contidos no arquivo informado'}
        else:
            client = client_from_files(args.tokens, args.config)
            pid = args.produto_id
            if args.sku:
                sku = args.sku.strip().upper()
                body = client.request_json('GET','/produtos?'+urlencode({'codigo':sku,'limit':100,'offset':0}))['body']
                product = next((p for p in body.get('itens',[]) if str(p.get('sku','')).strip().upper()==sku), None)
                if product is None:
                    raise ValueError('SKU exato nao encontrado no Tiny.')
                pid = product['id']
            rows = fetch_movements(client, pid, args.inicio.isoformat(), args.fim.isoformat())
            origin = {'origem':'api_oficial', 'produto_id':pid, 'sku':args.sku,
                      'inicio':args.inicio.isoformat(), 'fim':args.fim.isoformat()}
        report = {'status':'concluido', 'calculado_em':datetime.now().astimezone().isoformat(),
                  **origin, **calculate(rows)}
    except (Exception, SystemExit) as error:
        print(f'Nao foi possivel calcular: {error}\nNenhuma contagem foi confirmada.', file=sys.stderr)
        return 1
    if args.saida:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
