"""Bloqueios compartilhados e atualizacao explicita de unidades, sem escrita no Tiny."""
from datetime import datetime
import json
import threading
from urllib.parse import urlencode

from box_occupancy import BoxOccupancy


class WarehouseControls:
    def __init__(self,insights,request):
        self.insights=insights
        self.request=request
        self.occupancy=BoxOccupancy(insights,request)
        self.lock=threading.RLock()
        self.active=False
        with insights.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS warehouse_refresh(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL)')
            row=db.execute('SELECT data FROM warehouse_refresh WHERE id=1').fetchone()
        self.state=json.loads(row[0]) if row else {'status':'pronto'}
        if self.state.get('status')=='rodando':
            self.state.update(status='interrompido',mensagem='Atualizacao anterior interrompida; caixas ja salvas foram preservadas.')

    def locks(self):
        rows=[]
        offset=0
        while True:
            page=self.request('caixas_bloqueadas?'+urlencode({'select':'*','order':'setor,prateleira,caixa','limit':1000,'offset':offset}))
            if not isinstance(page,list):raise ValueError('Resposta de bloqueios invalida.')
            rows.extend(page)
            if len(page)<1000:return rows
            offset+=len(page)

    def set_lock(self,position,blocked):
        position=self.occupancy.position(position)
        if not isinstance(blocked,bool):raise ValueError('Informe bloquear ou desbloquear.')
        rows=self.request('rpc/definir_bloqueio_caixa',{'p_'+k:v for k,v in {**position,'bloqueada':blocked}.items()})
        if not isinstance(rows,list) or len(rows)!=1 or rows[0].get('bloqueada') is not blocked:
            raise ValueError('Bloqueio nao confirmado pelo Supabase.')
        return rows[0]

    def status(self):
        with self.lock:return {**self.state,'ativo':self.active}

    def update(self,**values):
        with self.lock:
            self.state.update(values)
            with self.insights.connect() as db:
                db.execute('INSERT OR REPLACE INTO warehouse_refresh VALUES(1,?)',(json.dumps(self.state),))

    def start(self):
        with self.lock:
            if self.active:raise ValueError('Uma atualizacao de unidades ja esta em andamento.')
            self.active=True
            last=self.state.get('ultimo_lote_completo_em')
            self.state={'status':'rodando','atual':0,'total':None,'salvas':0,'falhas':0,'erros':[],'ultimo_lote_completo_em':last,
                        'iniciado_em':datetime.now().astimezone().isoformat(),'mensagem':'Lendo caixas do Supabase...'}
        threading.Thread(target=self.run,daemon=True).start()
        return self.status()

    def run(self):
        saved=0
        errors=[]
        try:
            rows=[]
            offset=0
            while True:
                page=self.request('estoque?'+urlencode({'select':'sku,setor,prateleira,caixa','order':'sku','limit':1000,'offset':offset}))
                if not isinstance(page,list):raise ValueError('Lista de estoque invalida.')
                rows.extend(page)
                if len(page)<1000:break
                offset+=len(page)
            boxes={}
            for row in rows:
                position=self.occupancy.position(row)
                key=tuple(position.values())
                boxes.setdefault(key,set()).add(str(row['sku']).strip().upper())
            self.update(total=len(boxes))
            client=self.insights.client_factory() if boxes else None
            for index,(key,skus) in enumerate(boxes.items(),1):
                position=dict(zip(('setor','prateleira','caixa'),key))
                label=f'{key[0]}-{key[1]}{key[2]}'
                self.update(mensagem=f'{label}: consultando {len(skus)} SKUs...')
                try:
                    if len(skus)>150:raise ValueError('Caixa excede o limite de 150 SKUs por gravacao.')
                    started=datetime.now().astimezone().isoformat()
                    for sku in sorted(skus):self.insights.load_product(client,sku)
                    self.occupancy.publish(position,skus,started)
                    saved+=1
                except (Exception,SystemExit) as exc:
                    code=getattr(exc.__cause__ or exc,'code',None)
                    errors.append(f'{label}: consulta/gravacao nao confirmada'+(f' (HTTP {code})' if code else '.'))
                    if code in (401,403,429):
                        self.update(atual=index,salvas=saved,falhas=len(errors),erros=errors[-20:])
                        break
                self.update(atual=index,salvas=saved,falhas=len(errors),erros=errors[-20:])
            stamp=datetime.now().astimezone().isoformat()
            self.update(status='com_erros' if errors else 'concluido',finalizado_em=stamp,
                        mensagem=f'{saved} caixas salvas; {len(errors)} falhas.',
                        **({'ultimo_lote_completo_em':stamp} if not errors else {}))
        except (Exception,SystemExit):
            errors.append('Nao foi possivel concluir a atualizacao. Verifique acesso ao Tiny e Supabase.')
            self.update(status='com_erros',falhas=len(errors),erros=errors[-20:],mensagem=errors[-1])
        finally:
            with self.lock:self.active=False
