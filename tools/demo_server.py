"""Ambiente isolado para screenshots: todos os dados sao sinteticos, sem rede externa."""
import copy
import io
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://127.0.0.1:18866'
STAMP = '2026-10-22T14:30:00-03:00'
NAMES = ['Conector de engate rapido','Sensor de temperatura','Correia de transmissao','Interruptor bipolar','Rolamento blindado','Terminal de conexao']
STOCK = []
QUANTITIES=[1000,200,650,250,200,100,150,50,70,30,20,10,100,30,150,30,70,20,50,10,30,5,20,2,10,5,8,2,6,1,4,0,2,0,0,None]
JOBS={}
BOX_ACTIONS=[]
for index in range(36):
    STOCK.append({'sku':f'HL-{2048+index}','setor':f'{index//12+1}S','prateleira':f'P{index//6%2+1}',
                  'caixa':f'C{index//2%3+1}','lado':'','andar':'','nivel':['pouca','media','quase'][index%3],
                  'unidades_estoque':QUANTITIES[index],'unidades_atualizadas_em':STAMP if QUANTITIES[index] is not None else None,
                  'operador':['ANDERSON','LUIZ'][index%2],'atualizado_em':STAMP,'sincronizado_erp_em':STAMP})
STRUCTURE = [{'setor':str(s),'prateleira':str(p),'caixas':4,'capacidade_sku':8} for s in range(1,4) for p in range(1,3)]
SUMMARIES = [{'sku':r['sku'],'vendas':(37-i)*3 if i<30 else 0,'unidades':(37-i)*4 if i<30 else 0,
              'inicio':'2024-01-01','fim':'2026-10-22','atualizado_em':STAMP} for i,r in enumerate(STOCK)]
EVENTS = [{'sku':r['sku'],'acao':'alteracao' if i%3 else 'cadastro','posicao_anterior':'2S-P2C3' if i%3 else '',
           'posicao_nova':f"{r['setor']}-{r['prateleira']}{r['caixa']}",'operador':r['operador'],
           'criado_em':f'2026-10-22T14:{30-i:02d}:00-03:00','origem':'Demonstracao'} for i,r in enumerate(STOCK[:14])]
ITEMS = [{'id':i+1,'sku':f'HL-{2048+i}','description':NAMES[i],'expected':[12,20,8,50,10,16][i],
          'counted':[12,18,8,50,10,16][i],'checked':i<3,'printed_count':12 if i==0 else 0,'page':1,
          'bbox':{'x0':45,'x1':855,'top':380+i*76,'bottom':435+i*76,'page_width':900,'page_height':1200}}
         for i in range(6)]
NOTE = {'id':'demo','number':'000128','filename':'Conferencia_demonstrativa.pdf','pages':1,'items':ITEMS,
        'progress':50,'last_action':{'operator':'Anderson','created_at':STAMP},'total_items':6,'checked_items':3}


def page_image():
    from PIL import Image, ImageDraw, ImageFont
    page = Image.new('RGB',(900,1200),'white')
    draw = ImageDraw.Draw(page)
    def font(size):
        face=ImageFont.truetype(str(ROOT/'ui/fonts/PublicSans.ttf'),size)
        face.set_variation_by_axes([400])
        return face
    draw.text((45,48),'HL  |  CONFERENCIA DE MERCADORIAS',fill='#22252a',font=font(25))
    draw.text((45,98),'DOCUMENTO DEMONSTRATIVO - SEM VALOR FISCAL',fill='#737780',font=font(16))
    draw.line((45,145,855,145),fill='#d7dadd',width=2)
    draw.text((45,178),'NOTA 000128',fill='#22252a',font=font(32))
    draw.text((45,230),'Fornecedor demonstrativo  /  22 de outubro de 2026',fill='#555b63',font=font(19))
    draw.text((45,270),'Operacao: recebimento de mercadorias',fill='#555b63',font=font(18))
    draw.rectangle((45,334,855,370),fill='#f0f2f4')
    draw.text((58,342),'SKU',fill='#525760',font=font(15))
    draw.text((235,342),'DESCRICAO DO PRODUTO',fill='#525760',font=font(15))
    draw.text((770,342),'QTD.',fill='#525760',font=font(15))
    for i,item in enumerate(ITEMS):
        y=390+i*76
        draw.text((58,y),item['sku'],fill='#22252a',font=font(20))
        draw.text((235,y),item['description'],fill='#353a40',font=font(18))
        draw.text((790,y),str(item['expected']),fill='#22252a',font=font(20))
        draw.line((45,y+46,855,y+46),fill='#e5e8eb')
    draw.text((45,960),'6 produtos  |  116 unidades previstas',fill='#353a40',font=font(20))
    draw.text((45,1110),'Dados sinteticos utilizados exclusivamente para demonstracao da interface.',fill='#7c828a',font=font(15))
    result=io.BytesIO();page.save(result,format='PNG');return result.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,data,status=200,mime='application/json'):
        raw=json.dumps(data).encode() if mime=='application/json' else data.encode() if isinstance(data,str) else data
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def filtered(self,rows,q):
        rows=list(rows)
        if q.get('sku',[''])[0]:
            sku=q['sku'][0].removeprefix('eq.');rows=[r for r in rows if r.get('sku')==sku]
        offset=int(q.get('offset',['0'])[0]);limit=int(q.get('limit',['1000'])[0])
        return rows[offset:offset+limit]
    def do_GET(self):
        parsed=urlparse(self.path);path=parsed.path;q=parse_qs(parsed.query)
        if path=='/':
            html=(ROOT/'HL_COORDENADAS_v7.html').read_text(encoding='utf-8').replace('http://127.0.0.1:8766',ORIGIN).replace('http://127.0.0.1:8770/',ORIGIN+'/conference')
            return self.send(html,mime='text/html; charset=utf-8')
        if path=='/conference':return self.send((ROOT/'HL_Conferencia_NFe/static/index.html').read_text(encoding='utf-8'),mime='text/html; charset=utf-8')
        if path=='/ui/runtime-config.js':return self.send('window.HL_CONFIG='+json.dumps({'supabase_url':ORIGIN,'supabase_key':'demo-not-a-credential'})+';',mime='application/javascript')
        if path.startswith('/ui/'):
            file=(ROOT/path.lstrip('/')).resolve()
            if ROOT/'ui' not in file.parents:return self.send({},404)
            return self.send(file.read_bytes(),mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        if path in ('/style.css','/app.js'):
            file=ROOT/'HL_Conferencia_NFe/static'/path[1:]
            return self.send(file.read_bytes(),mime='text/css' if path.endswith('css') else 'application/javascript')
        if path=='/health':return self.send({'ok':True,'edicao':'leitor','token_salvo':False,'versao':'7.0'})
        if path=='/rest/v1/estoque':return self.send(self.filtered(STOCK,q))
        if path=='/rest/v1/estrutura':return self.send(STRUCTURE)
        if path=='/rest/v1/historico_movimentos':return self.send(self.filtered(EVENTS,q))
        if path=='/audit-events':return self.send({'ok':True,'eventos':self.filtered(EVENTS,q)})
        if path=='/audit-current':return self.send({'ok':True,'itens':self.filtered(STOCK,q)})
        if path=='/consultar':return self.send({'encontrado':True,'sku':q.get('sku',['HL-2048'])[0],'descricao':NAMES[0],'localizacao':'1S-P1C1','id':1})
        if path=='/sales-summary':return self.send({'ok':True,'itens':SUMMARIES})
        if path=='/sales-sync':return self.send({'ok':True,'ativo':False,'publicador':True,'status':'concluido','resultado':'com_erros','total':3,'atual':3,'publicados':2,'falhas':1,'mensagem':'2 SKUs confirmados no Supabase; 1 falha.','logs':[
            {'hora':STAMP,'sku':'HL-2048','etapa':'tiny','nivel':'info','mensagem':'Consultando produto e movimentos no Tiny.'},
            {'hora':STAMP,'sku':'HL-2048','etapa':'calculo','nivel':'info','mensagem':'150 movimentos lidos; 111 vendas; 148 unidades.'},
            {'hora':STAMP,'sku':'HL-2048','etapa':'supabase','nivel':'info','mensagem':'Enviando resumo ao Supabase; aguardando confirmacao.'},
            {'hora':STAMP,'sku':'HL-2048','etapa':'confirmado','nivel':'sucesso','mensagem':'SUCESSO: Supabase confirmou a gravacao deste SKU.'},
            {'hora':STAMP,'sku':'HL-2049','etapa':'confirmado','nivel':'sucesso','mensagem':'SUCESSO: Supabase confirmou a gravacao deste SKU.'},
            {'hora':STAMP,'sku':'HL-2050','etapa':'erro','nivel':'erro','mensagem':'HTTP 403: acesso recusado. Resumo anterior preservado.'}]})
        if path=='/box-insights-job':return self.send({'ok':True,**JOBS[q['id'][0]]})
        if path=='/__demo_state':return self.send({'acoes':BOX_ACTIONS,'estoque':STOCK})
        if path=='/api/settings':return self.send({'watch_dir':'Desktop/Notas fiscais','printer':'Impressora de etiquetas','printers':['Impressora de etiquetas'],'operators':[{'name':'Anderson'},{'name':'Luiz'}]})
        if path=='/api/notes':return self.send({'notes':[NOTE,{**NOTE,'id':'demo2','number':'000129','filename':'Recebimento_pendente.pdf','progress':0}]})
        if path.startswith('/api/notes/') and '/pages/' in path:return self.send(page_image(),mime='image/png')
        if path=='/api/notes/demo/history':return self.send({'history':[{'id':3,'action':'count','sku':'HL-2049','value':{'after':18},'operator':'Anderson','created_at':STAMP},{'id':2,'action':'toggle','sku':'HL-2048','value':{'after':1},'operator':'Luiz','created_at':STAMP}]})
        if path.startswith('/api/notes/'):return self.send(NOTE)
        if path=='/api/health':return self.send({'ok':True})
        return self.send([])
    def do_POST(self):
        path=urlparse(self.path).path
        payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
        if path=='/box-insights':
            BOX_ACTIONS.append(payload.get('acao','cache'))
            items=[]
            saved=[]
            for sku in payload.get('skus',[]):
                for i,r in enumerate(STOCK):
                    if r['sku']==sku:
                        if payload.get('acao')=='atualizar':
                            r['unidades_estoque']=(r['unidades_estoque'] or 0)+7
                            r['unidades_atualizadas_em']='2026-10-22T16:30:00-03:00'
                            saved.append({key:r[key] for key in ('sku','unidades_estoque','unidades_atualizadas_em')})
                        items.append({'sku':sku,'nome':NAMES[i%6],'disponivel':r['unidades_estoque'],'vendas':SUMMARIES[i]['vendas'],'unidades_vendidas':SUMMARIES[i]['unidades'],'historico_completo':True,'em_cache':True,'historico':[],'vendas_atualizado_em':STAMP})
            result={'ok':True,'itens':items,'consulta_pendente':False,'historico_completo':True}
            if saved:
                result['ocupacao']={'salvo':True,'posicao':payload['posicao'],'itens':saved,'unidades':sum(r['unidades_estoque'] for r in saved)}
                JOBS['demo-update']={'status':'concluido','mensagem':'Consulta finalizada','erros':[],'dados':result}
                return self.send({'ok':True,'job_id':'demo-update'})
            return self.send(result)
        if '/items/' in path:
            item=next(i for i in ITEMS if i['id']==int(path.rsplit('/',1)[1]))
            if payload.get('action')=='count':item['counted']=payload['value']
            if payload.get('action')=='toggle':item['checked']=not item['checked']
            return self.send(copy.deepcopy(NOTE))
        return self.send({'ok':True})


if __name__=='__main__':
    print('Demo isolada em '+ORIGIN,flush=True)
    ThreadingHTTPServer(('127.0.0.1',18866),Handler).serve_forever()
