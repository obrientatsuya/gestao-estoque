"""Estoque e vendas faturadas sob demanda; API Tiny somente de leitura."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import math
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlencode


class ImportPaused(Exception):
    pass


class ImportLimiter:
    def __init__(self, stop):
        self.stop = stop
        self.lock = threading.Lock()
        self.interval = 2.0
        self.next_call = 0.0

    def aguardar(self):
        with self.lock:
            if self.stop.wait(max(0, self.next_call - time.monotonic())):
                raise ImportPaused()
            self.next_call = time.monotonic() + self.interval

    def quota(self, headers):
        try:
            limit = int(headers.get('x-ratelimit-limit', 0))
            if limit > 0:
                with self.lock:
                    self.interval = 60 / max(1, min(60, limit // 2))
        except (ValueError, TypeError):
            pass


def number(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace('R$', '').strip()
        if ',' in value:
            value = value.replace('.', '').replace(',', '.')
    try:
        result = float(Decimal(str(value)))
    except InvalidOperation as exc:
        raise ValueError('Valor numerico invalido') from exc
    if not math.isfinite(result):
        raise ValueError('Valor numerico invalido')
    return result


class BoxInsights:
    def __init__(self, path, client_factory):
        self.path = str(path)
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.busy = False
        self.jobs = {}
        self.invoice_lock = threading.Lock()
        self.import_stop = threading.Event()
        self.import_job = {'status': 'parado'}
        self.history_start = '2000-01-01'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            # As contagens antigas de pedidos/movimentos nao sao vendas faturadas.
            db.executescript('''
              CREATE TABLE IF NOT EXISTS products(sku TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS invoice_sales(invoice_id TEXT, line_id TEXT,
                product_id TEXT, sku TEXT, date TEXT, quantity REAL, value REAL,
                invoice_number TEXT, generation TEXT,
                PRIMARY KEY(invoice_id,line_id,generation));
              CREATE INDEX IF NOT EXISTS invoice_sales_sku ON invoice_sales(generation,sku);
              CREATE INDEX IF NOT EXISTS invoice_sales_product ON invoice_sales(generation,product_id);
              CREATE TABLE IF NOT EXISTS invoice_state(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _state(db):
        row = db.execute('SELECT data FROM invoice_state WHERE id=1').fetchone()
        state = json.loads(row[0]) if row else {}
        return state if state.get('version') == 2 else {}

    def get_state(self):
        with self.connect() as db:
            return self._state(db)

    @staticmethod
    def _save_state(db, state):
        db.execute('INSERT OR REPLACE INTO invoice_state VALUES(1,?)', (json.dumps(state),))

    def cache(self, skus):
        items = []
        with self.connect() as db:
            db.execute('BEGIN')
            state = self._state(db)
            generation = state.get('active_generation') or state.get('generation') or ''
            known = bool(state.get('active_generation')) or bool(state.get('pages_ok'))
            for sku in skus:
                row = db.execute('SELECT data FROM products WHERE sku=?', (sku,)).fetchone()
                p = json.loads(row[0]) if row else {'sku':sku,'nome':sku,'disponivel':None}
                pid = str(p.get('id') or '')
                condition = '((generation=? AND sku=?) OR (generation=? AND product_id=?))' if pid else '(generation=? AND sku=?)'
                params = (generation,sku,generation,pid) if pid else (generation,sku)
                sales = db.execute(f'SELECT COUNT(DISTINCT invoice_id),COALESCE(SUM(quantity),0) '
                                   f'FROM invoice_sales WHERE {condition}', params).fetchone()
                history = db.execute(f'SELECT invoice_id,date,quantity,value,invoice_number FROM invoice_sales '
                                     f'WHERE {condition} ORDER BY date DESC,invoice_id DESC LIMIT 10', params).fetchall()
                confirmed = known and (sales[0] > 0 or bool(state.get('active_generation')))
                p.update(vendas=sales[0] if confirmed else None, unidades_vendidas=sales[1] if confirmed else None,
                         em_cache=bool(row), vendas_consultadas=known,
                         historico_completo=bool(state.get('active_generation')), erro_vendas=state.get('error'),
                         vendas_atualizado_em=state.get('updated_at'),
                         historico=[{'nota_id':h[0],'nota':h[4] or h[0],'data':h[1],
                                     'quantidade':h[2],'valor':h[3]} for h in history])
                items.append(p)
        items.sort(key=lambda p:(p['vendas'] is None,-(p['vendas'] or 0),-(p['unidades_vendidas'] or 0),p['sku']))
        return {'itens':items,'historico_completo':bool(state.get('active_generation')),
                'consulta_pendente':not bool(state.get('complete')),
                'notas_consultadas':state.get('processed',0), 'total_notas':state.get('total'),
                'criterio_vendas':'nota_saida_autorizada_com_valor', 'inicio_historico':self.history_start,
                'fonte_vendas':'Vendas faturadas; nao inclui saidas manuais sem nota fiscal.'}

    def load_product(self, client, sku):
        body = client.request_json('GET','/produtos?'+urlencode({'codigo':sku,'limit':100,'offset':0})).get('body') or {}
        candidates = body.get('itens')
        if not isinstance(candidates,list):
            raise ValueError('Resposta de produtos invalida')
        product = next((p for p in candidates if str(p.get('sku') or '').strip().upper()==sku),None)
        if not product:
            raise ValueError('SKU exato nao encontrado no Tiny')
        stock = client.request_json('GET',f"/estoque/{product['id']}").get('body') or {}
        available = number(stock.get('disponivel'))
        if available is None:
            raise ValueError('Tiny nao informou estoque disponivel')
        data = {'id':product['id'],'sku':sku,'nome':stock.get('nome') or product.get('descricao') or sku,
                'disponivel':available,'saldo':number(stock.get('saldo')),'reservado':number(stock.get('reservado')),
                'unidade':stock.get('unidade') or 'un.','atualizado_em':datetime.now().astimezone().isoformat()}
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO products VALUES(?,?)',(sku,json.dumps(data)))

    @staticmethod
    def parse_movement(item):
        """Mantem o calculador de arquivos de movimentos separado da fonte fiscal."""
        kind = str(item.get('tipo') or '').strip().upper()
        if kind not in ('S','SAIDA','SAÍDA'):
            if kind in ('E','B','ENTRADA','BALANCO','BALANÇO'):
                return None
            raise ValueError('Movimento sem tipo reconhecido; contagem nao confirmada')
        field = next((k for k in ('valor','precoUnitario','valorUnitario','preco') if item.get(k) is not None),None)
        if field is None:
            raise ValueError('Tiny nao informou o valor da saida; contagem nao confirmada')
        value = number(item[field])
        if value is None:
            raise ValueError('Tiny informou valor da saida invalido; contagem nao confirmada')
        if value <= 0:
            return None
        quantity = number(item.get('quantidade'))
        if quantity is None:
            raise ValueError('Movimento sem quantidade')
        if not quantity:
            return None
        mid = item.get('id')
        if mid is None:
            mid = item.get('idMovimento')
        if mid is None:
            raise ValueError('Movimento sem identificador; duplicidade nao pode ser controlada')
        return (str(mid),str(item.get('data') or item.get('dataHora') or ''),abs(quantity),value)

    @staticmethod
    def parse_invoice(note):
        if not isinstance(note,dict):
            raise ValueError('Nota fiscal invalida')
        if note.get('tipo') != 'S' or str(note.get('situacao')) not in ('6','7'):
            return []
        if note.get('id') is None or not isinstance(note.get('itens'),list):
            raise ValueError('Nota fiscal sem identificador ou sem itens; contagem nao confirmada')
        when = str(note.get('dataEmissao') or '')
        if when:
            try:
                when = datetime.fromisoformat(when).isoformat()
            except ValueError:
                when = datetime.strptime(when, '%d/%m/%Y').isoformat()
        parsed = []
        for index,item in enumerate(note['itens']):
            if not isinstance(item,dict):
                raise ValueError('Item fiscal invalido')
            pid = str(item.get('idProduto') or '')
            sku = str(item.get('codigo') or '').strip().upper()
            if not pid and not sku:
                continue
            quantity = number(item.get('quantidade'))
            total = number(item.get('valorTotal'))
            unit = number(item.get('valorUnitario'))
            if quantity is None or (total is None and unit is None):
                raise ValueError('Item fiscal sem quantidade ou valor; contagem nao confirmada')
            if quantity <= 0 or (total if total is not None else unit) <= 0:
                continue
            if unit is None:
                unit = total/quantity
            line = str(item['idItem']) if item.get('idItem') is not None else f'linha-{index}'
            parsed.append((str(note['id']),line,pid,sku,when,quantity,unit,str(note.get('numero') or '')))
        return parsed

    def _prepare_state(self, refresh=False):
        state = self.get_state()
        if state.get('complete') and not refresh:
            return state
        if not state or state.get('complete'):
            state = {'version':2,'snapshot':datetime.now().astimezone().isoformat(),'offset':0,
                     'processed':0,'pages_ok':0,'complete':False,'generation':uuid.uuid4().hex,
                     'active_generation':state.get('active_generation'),'updated_at':state.get('updated_at')}
        return state

    def _fetch_page(self, client, state, offset, limiter=None):
        params = {'tipo':'S','dataInicial':self.history_start,'dataFinal':state['snapshot'][:10],
                  'orderBy':state.get('order', 'desc'),'limit':100,'offset':offset}
        path = '/notas?' + urlencode(params)
        for attempt in range(5):
            try:
                if limiter and hasattr(client, 'request_read_json'):
                    response = client.request_read_json(path, limiter)
                else:
                    if limiter:
                        limiter.aguardar()
                    response = client.request_json('GET', path)
                if limiter:
                    limiter.quota(response.get('quota') or {})
                return response.get('body') or {}
            except ImportPaused:
                raise
            except Exception as exc:
                cause = exc.__cause__ or exc
                code = getattr(cause, 'code', None)
                retryable = code in (429, 500, 502, 503, 504) or isinstance(cause, (TimeoutError, ConnectionError))
                if not limiter or not retryable or attempt == 4:
                    raise
                headers = getattr(cause, 'headers', None) or {}
                try:
                    delay = max(2 ** (attempt + 1), float(headers.get('Retry-After') or (60 if code == 429 else 0)))
                except (ValueError, TypeError):
                    delay = 60
                if self.import_stop.wait(min(delay, 300)):
                    raise ImportPaused()

    def _commit_page(self, state, body):
        rows = body.get('itens')
        if not isinstance(rows,list):
            raise ValueError('Lista de notas invalida; contagem nao confirmada')
        parsed = [line for note in rows for line in self.parse_invoice(note)]
        pagination = body.get('paginacao') or body
        limit = int(pagination.get('limit') or 100)
        total = pagination.get('total')
        if limit <= 0 or len(rows) > limit or int(pagination.get('offset',state['offset'])) != state['offset']:
            raise ValueError('Paginacao de notas invalida')
        next_offset = state['offset']+len(rows)
        if total is not None and (int(total) < 0 or (not rows and next_offset < int(total))):
            raise ValueError('Pagina de notas incompleta')
        complete = next_offset >= int(total) if total is not None else len(rows)<limit
        next_state = dict(state,offset=next_offset,processed=next_offset,pages_ok=state['pages_ok']+1,
                          page_size=limit,total=int(total) if total is not None else None,complete=complete,error=None)
        if complete:
            next_state.update(active_generation=state['generation'],updated_at=datetime.now().astimezone().isoformat())
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO invoice_sales VALUES(?,?,?,?,?,?,?,?,?)',
                           [(*line,state['generation']) for line in parsed])
            if complete:
                db.execute('DELETE FROM invoice_sales WHERE generation<>?',(state['generation'],))
            self._save_state(db,next_state)
        return next_state

    def invoice_page(self,client,refresh=False):
        if self.import_status()['ativo']:
            return
        with self.invoice_lock:
            if self.import_status()['ativo']:
                return
            return self._single_page(client, refresh)

    def _single_page(self,client,refresh=False):
        state = self._prepare_state(refresh)
        if state.get('complete'):
            return
        try:
            self._commit_page(state, self._fetch_page(client, state, state['offset']))
        except Exception as exc:
            state['error'] = str(exc)
            with self.connect() as db:
                self._save_state(db,state)
            raise

    def import_status(self):
        state = self.get_state()
        with self.lock:
            job = dict(self.import_job)
        active = job['status'] in ('rodando', 'pausando')
        if job['status'] == 'parado':
            job['status'] = 'concluido' if state.get('complete') else ('pausado' if state.get('pages_ok') else 'parado')
        total, processed = state.get('total'), state.get('processed', 0)
        return {**job, 'ativo':active, 'notas':processed, 'total':total,
                'percentual':100 if state.get('complete') else min(99.9, processed / total * 100) if total else 0,
                'erro':job.get('erro') or state.get('error'), 'atualizado_em':state.get('updated_at')}

    def start_import(self):
        with self.lock:
            if self.import_job['status'] in ('rodando', 'pausando'):
                raise ValueError('A importacao ja esta em andamento neste PC.')
            if self.busy:
                raise ValueError('Aguarde a consulta da caixa terminar e tente novamente.')
            self.import_stop.clear()
            self.import_job = {'status':'rodando', 'erro':None}
        threading.Thread(target=self._run_import, daemon=True, name='notas-sqlite').start()
        return self.import_status()

    def pause_import(self):
        with self.lock:
            if self.import_job['status'] == 'rodando':
                self.import_job['status'] = 'pausando'
                self.import_stop.set()
        return self.import_status()

    def _run_import(self):
        status, error = 'pausado', None
        try:
            client = self.client_factory()
            limiter = ImportLimiter(self.import_stop)
            # Apenas duas paginas em memoria; commit em ordem e cursor atomico no SQLite.
            with self.invoice_lock, ThreadPoolExecutor(max_workers=2) as pool:
                state = self._prepare_state(refresh=True)
                if not state.get('pages_ok'):
                    state['order'] = 'asc'
                with self.connect() as db:
                    self._save_state(db, state)
                while not self.import_stop.is_set() and not state.get('complete'):
                    size = state.get('page_size', 100)
                    offsets = [state['offset']]
                    if state.get('pages_ok') and state.get('total') is not None and state['offset'] + size < state['total']:
                        offsets.append(state['offset'] + size)
                    futures = [(offset, pool.submit(self._fetch_page, client, dict(state), offset, limiter)) for offset in offsets]
                    for offset, future in futures:
                        try:
                            body = future.result()
                        except BaseException:
                            self.import_stop.set()
                            raise
                        if self.import_stop.is_set():
                            raise ImportPaused()
                        if state['offset'] != offset:
                            raise ValueError('Paginacao mudou. Retome para consultar novamente a pagina pendente.')
                        state = self._commit_page(state, body)
                        if state['complete']:
                            break
                if state.get('complete'):
                    status = 'concluido'
        except ImportPaused:
            pass
        except (Exception, SystemExit) as exc:
            status, error = 'erro', str(exc)
        finally:
            with self.lock:
                self.import_job = {'status':status, 'erro':error}

    def start(self,skus,refresh=False,history_only=False):
        with self.lock:
            if self.busy:
                raise ValueError('Outra caixa esta sendo consultada. Aguarde sua conclusao.')
            self.busy=True
            jid=uuid.uuid4().hex
            self.jobs=dict(list(self.jobs.items())[-29:])
            self.jobs[jid]={'status':'rodando','mensagem':'Consultando caixa...','erros':[]}
        threading.Thread(target=self.run,args=(jid,skus,refresh,history_only),daemon=True).start()
        return jid

    def run(self,jid,skus,refresh,history_only):
        errors=[]
        try:
            client=self.client_factory()
            cached={p['sku']:p for p in self.cache(skus)['itens']}
            if not history_only:
                for i,sku in enumerate(skus):
                    if cached[sku]['em_cache'] and not refresh:
                        continue
                    with self.lock:
                        self.jobs[jid]['mensagem']=f'Produto {i+1}/{len(skus)}: {sku}'
                    try:
                        self.load_product(client,sku)
                    except Exception as exc:
                        errors.append(f'{sku}: {exc}')
            if skus and (refresh or not self.get_state().get('complete')):
                self.invoice_page(client,refresh)
                state=self.get_state()
                with self.lock:
                    self.jobs[jid]['mensagem']=f"Notas fiscais: {state['processed']}/{state.get('total') or '?'}"
        except Exception as exc:
            errors.append(str(exc))
        finally:
            data=self.cache(skus)
            with self.lock:
                self.jobs[jid].update(status='concluido',mensagem='Consulta finalizada',erros=errors,dados=data)
                self.busy=False

    def job(self,jid):
        with self.lock:
            return dict(self.jobs.get(jid) or {})
