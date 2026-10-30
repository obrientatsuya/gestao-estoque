import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from box_insights import BoxInsights
from shared_sales import SharedSales


class Tiny:
    def __init__(self):
        self.calls = []
        self.denied = False

    def request_json(self, method, path):
        assert method == 'GET'
        assert '/notas' not in path
        self.calls.append(path)
        if '/produtos?' in path:
            return {'body':{'itens':[{'id':1,'sku':'001','descricao':'Produto'}]}}
        if 'logs-movimentacao' in path:
            if self.denied:
                error = HTTPError(path,403,'Forbidden',{},None)
                error.close()
                raise error
            return {'body':{'itens':[
                {'id':1,'tipo':'S','quantidade':3,'valor':10},
                {'id':1,'tipo':'S','quantidade':3,'valor':10},
                {'id':2,'tipo':'S','quantidade':4,'valor':0},
                {'id':3,'tipo':'E','quantidade':5,'valor':10},
                {'id':4,'tipo':'S','quantidade':2,'valor':7}],
                'paginacao':{'offset':0,'limit':100,'total':5}}}
        return {'body':{'disponivel':8,'saldo':8,'reservado':0,'nome':'Produto'}}


class TestShared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = Tiny()
        self.insights = BoxInsights(Path(self.tmp.name)/'cache.db',lambda:self.client)
        self.service = SharedSales(self.insights,'https://example.invalid','public',self.tmp.name)
        self.cloud = {}
        self.published = []

        def request(path,payload=None):
            if path.startswith('rpc/'):
                self.published.append(payload)
                self.assertEqual(set(payload),{'p_chave','p_resumos'})
                for r in payload['p_resumos']:
                    self.assertEqual(set(r),{'sku','vendas','unidades','inicio','fim','calculado_em'})
                    self.cloud[r['sku']] = {**r,'atualizado_em':r['calculado_em']}
                return [self.cloud[r['sku']] for r in payload['p_resumos']]
            if path.startswith('estoque?'):
                return [{'sku':'001'}]
            return list(self.cloud.values())

        self.service.request = request

    def tearDown(self):
        self.tmp.cleanup()

    def done(self,jid):
        deadline = time.monotonic()+4
        while self.service.job(jid)['status']!='concluido' and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(self.service.job(jid)['status'],'concluido')
        return self.service.job(jid)

    def test_reader_only_reads_summary_without_tiny_authentication(self):
        self.cloud['001']={'sku':'001','vendas':3,'unidades':5}
        job=self.done(self.service.start(['001'],refresh=True))
        self.assertEqual(job['dados']['itens'][0]['vendas'],3)
        self.assertFalse(self.client.calls)
        self.assertFalse(self.published)

    def test_reader_box_refresh_reads_stock_but_never_logs_or_invoices(self):
        position={'setor':'1S','prateleira':'P1','caixa':'C1'}
        with patch.object(self.service.occupancy,'publish',return_value={'salvo':True,'unidades':8}) as publish:
            job=self.done(self.service.start(['001'],refresh=True,position=position))
        self.assertTrue(job['dados']['ocupacao']['salvo'])
        publish.assert_called_once()
        self.assertTrue(any('/estoque/' in path for path in self.client.calls))
        self.assertFalse(any('/notas' in path or '/logs-movimentacao' in path for path in self.client.calls))
        self.assertFalse(self.published)

    def test_failed_stock_query_preserves_occupancy_without_publication(self):
        self.client.request_json=lambda *args:(_ for _ in ()).throw(ValueError('Tiny offline'))
        with patch.object(self.service.occupancy,'publish') as publish:
            job=self.done(self.service.start(['001'],refresh=True,position={'setor':'1S','prateleira':'P1','caixa':'C1'}))
        publish.assert_not_called()
        self.assertNotIn('ocupacao',job['dados'])
        self.assertTrue(job['erros'])

    def test_unknown_is_not_zero_and_old_invoice_counts_are_not_used(self):
        data=self.service.cache(['001'])
        self.assertIsNone(data['itens'][0]['vendas'])
        self.assertFalse(data['historico_completo'])

    def test_publisher_uploads_only_aggregates_and_tiny_is_get_only(self):
        with patch.object(self.service,'publisher_key',return_value='test-key'):
            job=self.done(self.service.start(['001'],refresh=True))
        self.assertEqual(job['erros'],[])
        self.assertEqual(self.cloud['001']['vendas'],2)
        self.assertEqual(self.cloud['001']['unidades'],5)
        self.assertEqual(len(self.published),1)
        self.assertTrue(any('logs-movimentacao' in p for p in self.client.calls))
        self.assertFalse(any('/notas' in p for p in self.client.calls))

    def test_failure_preserves_previous_summary_and_does_not_publish_zero(self):
        self.cloud['001']={'sku':'001','vendas':42,'unidades':50}
        self.client.denied=True
        with patch.object(self.service,'publisher_key',return_value='test-key'):
            job=self.done(self.service.start(['001'],refresh=True))
        self.assertIn('403',job['erros'][0])
        self.assertEqual(job['dados']['itens'][0]['vendas'],42)
        self.assertFalse(self.published)

    def test_missing_price_is_not_published(self):
        original=self.client.request_json
        def no_value(method,path):
            response=original(method,path)
            if 'logs-movimentacao' in path:
                for r in response['body']['itens']:
                    r.pop('valor',None)
            return response
        self.client.request_json=no_value
        with patch.object(self.service,'publisher_key',return_value='test-key'):
            job=self.done(self.service.start(['001'],refresh=True))
        self.assertTrue(job['erros'])
        self.assertFalse(self.published)

    def test_explicit_bulk_reads_skus_not_invoices(self):
        with patch.object(self.service,'publisher_key',return_value='test-key'):
            job=self.done(self.service.start(None,refresh=True))
        self.assertEqual(job['total'],1)
        self.assertEqual(len(self.published),1)

    def test_cloud_pagination_beyond_1000(self):
        calls=[]
        def pages(path,payload=None):
            calls.append(path)
            return [{'sku':str(i),'vendas':0,'unidades':0} for i in range(1000)] if len(calls)==1 else [{'sku':'last','vendas':1,'unidades':1}]
        self.service.request=pages
        self.assertEqual(len(self.service.all_shared()),1001)
        self.assertIn('offset=1000',calls[1])

    def test_sales_success_is_logged_only_after_cloud_confirmation_and_survives_restart(self):
        with patch.object(self.service,'publisher_key',return_value='test-key'):
            job=self.done(self.service.start(['001'],refresh=True))
            self.assertEqual(job['publicados'],1)
            self.assertEqual(job['falhas'],0)
            self.assertEqual([e['etapa'] for e in job['logs']],['inicio','tiny','calculo','supabase','confirmado','fim'])
            restarted=SharedSales(self.insights,'https://example.invalid','public',self.tmp.name,{'edicao':'publicador','chave_publicador':'test-key'})
            status=restarted.status()
            self.assertEqual(status['publicados'],1)
            self.assertFalse(status['ativo'])
            self.assertEqual(status['logs'][-1]['etapa'],'fim')

    def test_sales_failure_is_visible_before_completion_and_never_reports_success(self):
        self.client.denied=True
        with patch.object(self.service,'publisher_key',return_value='test-key'):
            job=self.done(self.service.start(['001'],refresh=True))
        self.assertEqual(job['publicados'],0)
        self.assertEqual(job['falhas'],1)
        self.assertEqual(job['resultado'],'com_erros')
        self.assertTrue(any(e['nivel']=='erro' and '403' in e['mensagem'] for e in job['logs']))
        self.assertFalse(any(e['etapa']=='confirmado' for e in job['logs']))

    def test_reader_stock_update_does_not_create_sales_logs(self):
        self.done(self.service.start(['001'],refresh=True))
        with self.insights.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sales_publication_log').fetchone()[0],0)

    def test_diagnostic_only_reads_one_page_and_does_not_publish(self):
        with patch.object(self.service,'publisher_key',return_value='test-key'):
            report=self.service.diagnostic('001')
        self.assertEqual(report['sku'],'001')
        self.assertIn('itens',report['resposta'])
        self.assertTrue(any('limit=20' in path for path in self.client.calls))
        self.assertFalse(self.published)

    def test_diagnostic_excludes_credentials_free_text_and_identifiers(self):
        report=self.service.save_diagnostic('001',{'access_token':'VERY_PRIVATE_TOKEN','cliente':{'nome':'PRIVATE_NAME','cpf':'PRIVATE_CPF'},'id':123456,'valorVenda':'38,22','tipo':'S','extra':{'authorization':'VERY_PRIVATE_AUTH'}})
        serialized=json.dumps(report)
        for value in ('VERY_PRIVATE_TOKEN','PRIVATE_NAME','PRIVATE_CPF','123456','VERY_PRIVATE_AUTH'):
            self.assertNotIn(value,serialized)
        self.assertEqual(report['resposta']['valorVenda'],'38,22')
        self.assertEqual(report['resposta']['tipo'],'S')


if __name__=='__main__':
    unittest.main()
