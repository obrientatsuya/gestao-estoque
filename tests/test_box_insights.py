import sys
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import parse_qs,urlparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from box_insights import BoxInsights,number


def line(pid,sku,quantity=1,value=12,line_id=1):
    return {'idItem':line_id,'idProduto':pid,'codigo':sku,'quantidade':quantity,
            'valorUnitario':value,'valorTotal':quantity*value}


def note(nid,items,kind='S',status=6):
    return {'id':nid,'numero':str(1000+nid),'tipo':kind,'situacao':str(status),
            'dataEmissao':f'2026-10-{nid:02d}','itens':items}


class FakeTiny:
    def __init__(self):
        self.calls=[]
        self.notes=[note(1,[line(10,'001',2,38.22),line(10,'001',1,12,2)]),
                    note(2,[line(10,'001',8,0)]),note(3,[line(10,'001',7,100)],kind='E'),
                    note(4,[line(10,'001',9,100)],status=3),note(5,[line(11,'002')]),
                    note(6,[line(10,'001',1,-1)]),note(7,[line(10,'001',2,15)],status=7)]

    def request_json(self,method,path):
        assert method=='GET','Nenhuma escrita e permitida'
        self.calls.append(path)
        url=urlparse(path)
        query=parse_qs(url.query)
        if url.path=='/produtos':
            sku=query['codigo'][0]
            return {'body':{'itens':[{'id':10 if sku=='001' else 11,'sku':sku,'descricao':'Nome '+sku}]}}
        if url.path=='/notas':
            assert query['tipo']==['S'] and 'situacao' not in query
            offset=int(query['offset'][0])
            return {'body':{'itens':self.notes[offset:offset+2],
                            'paginacao':{'limit':2,'offset':offset,'total':len(self.notes)}}}
        assert url.path.startswith('/estoque/') and not url.path.endswith('/logs-movimentacao')
        return {'body':{'disponivel':0,'reservado':4,'saldo':4,'nome':'Produto','unidade':'un.'}}


class TestInsights(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.client=FakeTiny()
        self.cache=BoxInsights(Path(self.tmp.name)/'cache.sqlite3',lambda:self.client)
        self.cache.load_product(self.client,'001')
        self.cache.load_product(self.client,'002')

    def tearDown(self):
        self.tmp.cleanup()

    def complete(self):
        for _ in range(4):
            self.cache.invoice_page(self.client)

    def test_cache_without_network_and_old_counts_are_not_used(self):
        with self.cache.connect() as db:
            db.executescript('CREATE TABLE orders(id TEXT); INSERT INTO orders VALUES("79");')
        count=len(self.client.calls)
        other=BoxInsights(self.cache.path,lambda:self.client)
        item=other.cache(['001'])['itens'][0]
        self.assertIsNone(item['vendas'])
        self.assertEqual(item['disponivel'],0)
        self.assertEqual(len(self.client.calls),count)

    def test_only_authorized_positive_value_invoice_sales_count(self):
        self.complete()
        data=self.cache.cache(['002','001'])
        self.assertEqual([p['sku'] for p in data['itens']],['001','002'])
        item=data['itens'][0]
        self.assertEqual(item['vendas'],2)
        self.assertEqual(item['unidades_vendidas'],5)
        self.assertTrue(item['historico_completo'])
        self.assertEqual({h['nota_id'] for h in item['historico']},{'1','7'})
        self.assertEqual(data['notas_consultadas'],7)
        self.assertEqual(data['criterio_vendas'],'nota_saida_autorizada_com_valor')
        self.assertEqual(number('1.038,22'),1038.22)

    def test_resume_on_restart_and_no_second_invoice_scan_for_other_box(self):
        self.cache.invoice_page(self.client)
        self.assertFalse(self.cache.cache(['001'])['historico_completo'])
        self.cache=BoxInsights(self.cache.path,lambda:self.client)
        self.complete()
        count=len(self.client.calls)
        self.cache.invoice_page(self.client)
        self.assertEqual(count,len(self.client.calls))
        jid=self.cache.start(['002'])
        deadline=time.monotonic()+3
        while self.cache.job(jid)['status']!='concluido' and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(self.cache.job(jid)['dados']['itens'][0]['vendas'],1)
        self.assertEqual(count,len(self.client.calls))

    def test_refresh_keeps_previous_complete_cache_until_replacement(self):
        self.complete()
        self.client.notes[0]['itens'][0]['valorTotal']=0
        self.client.notes[0]['itens'][1]['valorTotal']=0
        self.cache.invoice_page(self.client,True)
        self.assertEqual(self.cache.cache(['001'])['itens'][0]['vendas'],2)
        self.assertTrue(self.cache.cache(['001'])['consulta_pendente'])
        self.complete()
        self.assertEqual(self.cache.cache(['001'])['itens'][0]['vendas'],1)

    def test_api_failure_does_not_become_zero_and_retains_previous_cache(self):
        class Forbidden:
            def request_json(self,*args):
                raise RuntimeError('HTTP 403: Forbidden')
        with self.assertRaisesRegex(RuntimeError,'403'):
            self.cache.invoice_page(Forbidden())
        self.assertIsNone(self.cache.cache(['001'])['itens'][0]['vendas'])
        self.complete()
        with self.assertRaisesRegex(RuntimeError,'403'):
            self.cache.invoice_page(Forbidden(),True)
        item=self.cache.cache(['001'])['itens'][0]
        self.assertEqual(item['vendas'],2)
        self.assertIn('403',item['erro_vendas'])

    def test_missing_invoice_items_does_not_confirm_zero(self):
        self.client.notes[0]['itens']=None
        with self.assertRaisesRegex(ValueError,'itens'):
            self.cache.invoice_page(self.client)
        self.assertIsNone(self.cache.cache(['001'])['itens'][0]['vendas'])

    def test_missing_value_does_not_confirm_zero(self):
        for field in ('valorTotal','valorUnitario'):
            self.client.notes[0]['itens'][0].pop(field)
        with self.assertRaisesRegex(ValueError,'valor'):
            self.cache.invoice_page(self.client)
        self.assertIsNone(self.cache.cache(['001'])['itens'][0]['vendas'])

    def test_empty_history_is_known_zero(self):
        self.client.notes=[]
        self.cache.invoice_page(self.client)
        item=self.cache.cache(['001'])['itens'][0]
        self.assertEqual(item['vendas'],0)
        self.assertTrue(item['historico_completo'])

    def test_opening_and_reading_cache_do_not_start_network(self):
        count=len(self.client.calls)
        other=BoxInsights(self.cache.path,lambda:self.client)
        other.cache(['001','002'])
        time.sleep(.03)
        self.assertEqual(len(self.client.calls),count)

    def test_explicit_action_reads_only_one_invoice_page(self):
        before=len(self.client.calls)
        jid=self.cache.start(['001'])
        deadline=time.monotonic()+3
        while self.cache.job(jid)['status']!='concluido' and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(self.cache.job(jid)['status'],'concluido')
        self.assertEqual(len(self.client.calls)-before,1)
        self.assertEqual(self.cache.get_state()['processed'],2)
        self.assertTrue(self.cache.job(jid)['dados']['consulta_pendente'])
        time.sleep(.03)
        self.assertEqual(len(self.client.calls)-before,1)

    def test_partial_history_does_not_claim_zero_sales(self):
        self.cache.invoice_page(self.client)
        item=self.cache.cache(['002'])['itens'][0]
        self.assertIsNone(item['vendas'])
        self.assertFalse(item['historico_completo'])

    def test_zero_total_is_excluded_even_when_unit_price_is_positive(self):
        item=line(10,'001',1,38.22)
        item['valorTotal']=0
        self.assertEqual(BoxInsights.parse_invoice(note(1,[item])),[])
        item['valorTotal']=38.22
        item['quantidade']=0
        self.assertEqual(BoxInsights.parse_invoice(note(1,[item])),[])

    def test_exact_sku_fallback_does_not_match_similar_sku(self):
        self.client.notes=[note(1,[line(None,'0010'),line(None,'001',2,line_id=2)])]
        self.cache.invoice_page(self.client)
        self.assertEqual(self.cache.cache(['001'])['itens'][0]['unidades_vendidas'],2)


if __name__=='__main__':
    unittest.main()
