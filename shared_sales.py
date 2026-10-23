"""Resumo compartilhado: Tiny somente GET; Supabase recebe apenas agregados."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import threading
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from calcular_saidas_tiny import calculate, fetch_movements
from box_occupancy import BoxOccupancy


class SharedSales:
    def __init__(self, insights, url, public_key, data_dir, edition=None):
        self.insights = insights
        self.url = url.rstrip('/')
        self.public_key = public_key
        self.data_dir = Path(data_dir)
        self.edition = dict(edition or {})
        self.lock = threading.RLock()
        self.jobs = {}
        self.busy = False
        self.stop = threading.Event()
        self.current_job = None
        self.occupancy = BoxOccupancy(insights,lambda path,payload:self.request(path,payload))
        with insights.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS shared_sales(sku TEXT PRIMARY KEY,data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sales_publication_log(id INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sales_publication_state(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL)')

    def sales_event(self,jid,stage,message,sku='',level='info',**values):
        event={'hora':datetime.now().astimezone().isoformat(),'etapa':stage,'mensagem':message,'sku':sku,'nivel':level,**values}
        with self.lock:
            job=self.jobs[jid]
            job['mensagem']=message
            events=job.setdefault('logs',[])
            events.append(event)
            del events[:-100]
            snapshot={k:v for k,v in job.items() if k!='dados'}
            with self.insights.connect() as db:
                db.execute('INSERT INTO sales_publication_log(job,data) VALUES(?,?)',(jid,json.dumps(event)))
                db.execute('DELETE FROM sales_publication_log WHERE id <= (SELECT max(id)-2000 FROM sales_publication_log)')
                db.execute('INSERT OR REPLACE INTO sales_publication_state VALUES(1,?)',(json.dumps(snapshot),))

    def publisher_key(self):
        if self.edition.get('edicao') == 'leitor':
            return ''
        if self.edition.get('edicao') == 'publicador':
            return str(self.edition.get('chave_publicador') or '').strip()
        key = os.getenv('HL_VENDAS_PUBLICADOR', '').strip()
        path = self.data_dir / 'vendas_publicador.json'
        if not key and path.is_file():
            try:
                key = str(json.loads(path.read_text(encoding='utf-8')).get('chave') or '').strip()
            except (ValueError, OSError):
                return ''
        return key

    def request(self, path, payload=None):
        headers = {'apikey':self.public_key, 'Authorization':'Bearer '+self.public_key,
                   'Content-Type':'application/json'}
        request = Request(self.url+'/rest/v1/'+path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers=headers, method='POST' if payload is not None else 'GET')
        try:
            with urlopen(request, timeout=40) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except Exception as exc:
            code = getattr(exc, 'code', None)
            if path.startswith(('caixas_bloqueadas','rpc/definir_bloqueio_caixa')):
                if code in (400,404):raise ValueError('Execute supabase_bloqueio_caixas.sql no SQL Editor para habilitar o bloqueio compartilhado.') from None
                raise ValueError('Nao foi possivel confirmar o bloqueio no Supabase. Confira conexao e permissoes.') from None
            if path.startswith('rpc/atualizar_ocupacao_caixa'):
                detail = ''
                if hasattr(exc,'read'):
                    try:detail = json.loads(exc.read()).get('message','')
                    except (ValueError,OSError):pass
                if code in (400,404) and ('function' in detail.lower() or 'schema cache' in detail.lower()):
                    raise ValueError('Execute supabase_ocupacao_unidades.sql no SQL Editor para compartilhar a ocupacao.') from None
                raise ValueError('Ocupacao nao salva no Supabase: '+(detail or 'verifique conexao e permissoes.')) from None
            if code in (400, 404):
                raise ValueError('Configure supabase_resumo_vendas.sql no SQL Editor do Supabase.') from None
            if code in (401, 403):
                raise ValueError('Supabase recusou o acesso ao resumo. Confira a chave do publicador e as permissoes da tabela.') from None
            raise ValueError('Nao foi possivel acessar o resumo compartilhado no Supabase.') from None

    def save_shared(self, rows):
        with self.insights.connect() as db:
            db.executemany('INSERT OR REPLACE INTO shared_sales VALUES(?,?)',
                           [(r['sku'], json.dumps(r)) for r in rows])

    def all_shared(self):
        rows, offset = [], 0
        while True:
            page = self.request('resumo_vendas?'+urlencode({'select':'*','order':'sku.asc','limit':1000,'offset':offset}))
            if not isinstance(page, list):
                raise ValueError('Resposta do resumo invalida')
            rows.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
        self.save_shared(rows)
        return rows

    def cache(self, skus):
        error = None
        if skus:
            quoted = ','.join('"'+s.replace('\\','\\\\').replace('"','\\"')+'"' for s in skus)
            try:
                rows = self.request('resumo_vendas?'+urlencode({'select':'*','sku':'in.('+quoted+')','limit':150}))
                if not isinstance(rows,list):
                    raise ValueError('Resposta do resumo invalida')
                self.save_shared(rows)
            except ValueError as exc:
                error = str(exc)
        items = []
        with self.insights.connect() as db:
            for sku in skus:
                row = db.execute('SELECT data FROM products WHERE sku=?',(sku,)).fetchone()
                p = json.loads(row[0]) if row else {'sku':sku,'nome':sku,'disponivel':None}
                record = db.execute('SELECT data FROM shared_sales WHERE sku=?',(sku,)).fetchone()
                summary = json.loads(record[0]) if record else {}
                p.update(vendas=summary.get('vendas'), unidades_vendidas=summary.get('unidades'),
                         em_cache=bool(row), historico_completo=bool(record), historico=[],
                         vendas_consultadas=bool(record), erro_vendas=error,
                         vendas_atualizado_em=summary.get('atualizado_em'),
                         inicio=summary.get('inicio'), fim=summary.get('fim'))
                items.append(p)
        items.sort(key=lambda p:(p['vendas'] is None, -(p['vendas'] or 0), p['sku']))
        return {'itens':items,'consulta_pendente':False,'fonte_vendas':'Saidas com valor positivo; resumo compartilhado',
                'historico_completo':all(p['vendas'] is not None for p in items),
                'publicador':bool(self.publisher_key()),'erro_compartilhado':error}

    def start(self, skus, refresh=False, history_only=False, position=None):
        if position is not None:
            position = self.occupancy.position(position)
        with self.lock:
            if self.busy:
                raise ValueError('Uma atualizacao de vendas ja esta em andamento neste PC.')
            self.busy = True
            self.stop.clear()
            jid = uuid.uuid4().hex
            self.jobs = dict(list(self.jobs.items())[-29:])
            self.jobs[jid] = {'status':'rodando','mensagem':'Lendo resumo compartilhado...','erros':[],
                              'publicados':0,'falhas':0,'logs':[]}
            self.current_job = jid
        threading.Thread(target=self.run,args=(jid,skus,refresh,position),daemon=True).start()
        return jid

    def run(self, jid, skus, refresh, position=None):
        errors = []
        occupancy = None
        fresh = set()
        try:
            key = self.publisher_key()
            if refresh and key:self.sales_event(jid,'inicio','Consulta de vendas iniciada. Cada SKU e enviado imediatamente apos o calculo.')
            if skus is None:
                if not key:
                    raise ValueError('Configure a chave do publicador neste PC antes de consultar os logs.')
                skus, offset = [], 0
                while True:
                    rows = self.request('estoque?'+urlencode({'select':'sku','order':'sku.asc','limit':1000,'offset':offset}))
                    if not isinstance(rows,list):
                        raise ValueError('Lista de SKUs invalida')
                    skus.extend(str(r['sku']).strip().upper() for r in rows)
                    self.sales_event(jid,'lista',f'{len(skus)} SKUs encontrados no Supabase.')
                    if len(rows)<1000:
                        break
                    offset += len(rows)
                skus = sorted(set(skus))
            with self.lock:
                self.jobs[jid].update(total=len(skus),atual=0)
            if refresh and position is not None:
                started = datetime.now().astimezone().isoformat()
                client = self.insights.client_factory()
                for index,sku in enumerate(skus):
                    if self.stop.is_set():break
                    with self.lock:self.jobs[jid]['mensagem'] = f'Estoque da caixa: {index+1}/{len(skus)} · {sku}'
                    try:
                        self.insights.load_product(client,sku)
                        fresh.add(sku)
                    except (Exception,SystemExit) as exc:
                        errors.append(f'Estoque de {sku}: {exc}')
                if fresh == set(skus) and not self.stop.is_set():
                    try:
                        # A RPC grava somente unidades; localizacao, operador, datas de movimento e ERP nao mudam.
                        occupancy = self.occupancy.publish(position,skus,started)
                    except (Exception,SystemExit) as exc:errors.append(str(exc))
                else:errors.append('Ocupacao anterior preservada: a consulta de estoque ficou incompleta.')
            if refresh and key:
                client = self.insights.client_factory()
                start, end = '2000-01-01', date.today().isoformat()
                for index, sku in enumerate(skus):
                    if self.stop.is_set():
                        break
                    with self.lock:
                        self.jobs[jid]['mensagem'] = f'Logs de estoque: {index+1}/{len(skus)} · {sku}'
                    try:
                        self.sales_event(jid,'tiny','Consultando produto e movimentos no Tiny.',sku)
                        if position is None:self.insights.load_product(client,sku)
                        elif sku not in fresh:continue
                        with self.insights.connect() as db:
                            product = json.loads(db.execute('SELECT data FROM products WHERE sku=?',(sku,)).fetchone()[0])
                        rows = fetch_movements(client,product['id'],start,end)
                        if self.stop.is_set():
                            break
                        totals = calculate(rows)
                        self.sales_event(jid,'calculo',f"{totals['registros_lidos']} movimentos lidos; {totals['vendas']} vendas; {totals['unidades_vendidas']:g} unidades.",sku)
                        summary = {'sku':sku,'vendas':totals['vendas'],'unidades':totals['unidades_vendidas'],
                                   'inicio':start,'fim':end,'calculado_em':datetime.now().astimezone().isoformat()}
                        # Somente o agregado sai deste PC. Logs, valores, clientes e tokens nao sao publicados.
                        self.sales_event(jid,'supabase','Enviando resumo ao Supabase; aguardando confirmacao.',sku)
                        result = self.request('rpc/publicar_resumo_vendas',{'p_chave':key,'p_resumos':[summary]})
                        if not isinstance(result,list) or len(result)!=1 or result[0].get('sku')!=sku:
                            raise ValueError('Publicacao nao confirmada pelo Supabase')
                        self.save_shared(result)
                        with self.lock:self.jobs[jid]['publicados']+=1
                        self.sales_event(jid,'confirmado','SUCESSO: Supabase confirmou a gravacao deste SKU.',sku,'sucesso',vendas=totals['vendas'],unidades=totals['unidades_vendidas'])
                    except (Exception,SystemExit) as exc:
                        code = getattr(exc.__cause__ or exc, 'code', None)
                        message = 'Tiny recusou os logs (403). Resumo anterior preservado.' if code == 403 else str(exc)
                        errors.append(f'{sku}: {message}')
                        with self.lock:
                            self.jobs[jid]['falhas']+=1
                            self.jobs[jid]['erros']=list(errors)
                        safe=f'HTTP {code}: acesso recusado ou limite da API. Resumo anterior preservado.' if code else 'Falha na consulta ou publicacao. Resumo anterior preservado.'
                        self.sales_event(jid,'erro',safe,sku,'erro')
                        with self.lock:self.jobs[jid]['atual']=index+1
                        if code in (401,403,429):
                            break
                    with self.lock:
                        self.jobs[jid]['atual'] = index+1
        except (Exception,SystemExit) as exc:
            errors.append(str(exc))
            if refresh and self.publisher_key():
                with self.lock:self.jobs[jid]['falhas']+=1
                self.sales_event(jid,'erro','Nao foi possivel concluir a consulta de vendas. Confira autorizacao e conexao.','', 'erro')
        finally:
            data = self.cache((skus or [])[:150])
            if occupancy:data['ocupacao'] = occupancy
            with self.lock:
                self.jobs[jid].update(status='concluido',mensagem='Atualizacao finalizada',erros=errors,dados=data)
                self.busy = False
            if refresh and self.publisher_key():
                with self.lock:
                    job=self.jobs[jid]
                    state='pausado' if self.stop.is_set() else 'com_erros' if errors else 'concluido'
                    job['resultado']=state
                    message=f"{state.upper()}: {job['publicados']} SKUs confirmados no Supabase; {job['falhas']} falhas."
                self.sales_event(jid,'fim',message,level='erro' if errors else 'info')

    def status(self, pause=False):
        with self.lock:
            if pause:
                self.stop.set()
            job = {k:v for k,v in (self.jobs.get(self.current_job) or {}).items() if k != 'dados'}
            if not job and self.publisher_key():
                with self.insights.connect() as db:record=db.execute('SELECT data FROM sales_publication_state WHERE id=1').fetchone()
                if record:
                    job=json.loads(record[0])
                    if job.get('status')=='rodando':
                        job.update(status='interrompido',mensagem='Execucao anterior interrompida. Os SKUs ja confirmados continuam no Supabase.')
            return {**job,'ativo':self.busy,'publicador':bool(self.publisher_key())}

    def job(self, jid):
        with self.lock:
            return dict(self.jobs.get(jid) or {})
