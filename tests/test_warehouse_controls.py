from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from box_insights import BoxInsights
from warehouse_controls import WarehouseControls


class TestWarehouseControls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.insights=BoxInsights(Path(self.tmp.name)/'cache.db',lambda:object())
        self.rows=[dict(sku='001',setor='1S',prateleira='P1',caixa='C1'),dict(sku='002',setor='1S',prateleira='P1',caixa='C1'),dict(sku='003',setor='1S',prateleira='P1',caixa='C2')]
        self.calls=[]
        def request(path,payload=None):
            self.calls.append((path,payload))
            if path.startswith('estoque?'):return self.rows
            if path=='rpc/atualizar_ocupacao_caixa':
                return [dict(sku=r['sku'],unidades_estoque=r['unidades'],unidades_atualizadas_em=payload['p_consultado_em']) for r in payload['p_itens']]
            if path=='rpc/definir_bloqueio_caixa':return [dict(bloqueada=payload['p_bloqueada'])]
            return []
        self.service=WarehouseControls(self.insights,request)
        def load(client,sku):
            with self.insights.connect() as db:db.execute('INSERT OR REPLACE INTO products VALUES(?,?)',(sku,json.dumps(dict(sku=sku,disponivel=8,atualizado_em=datetime.now(timezone.utc).isoformat()))))
        self.insights.load_product=load

    def tearDown(self):self.tmp.cleanup()

    def test_all_boxes_publish_separately_and_remember_completion(self):
        self.service.active=True
        self.service.run()
        state=self.service.status()
        self.assertEqual(state['salvas'],2)
        self.assertEqual(state['status'],'concluido')
        self.assertFalse(state['ativo'])
        writes=[p for path,p in self.calls if path.startswith('rpc/')]
        self.assertEqual(len(writes),2)
        self.assertEqual(len(writes[0]['p_itens']),2)
        self.assertNotIn('p_chave',writes[0])
        restored=WarehouseControls(self.insights,self.service.request)
        self.assertEqual(restored.status()['ultimo_lote_completo_em'],state['ultimo_lote_completo_em'])

    def test_incomplete_box_is_not_published_but_other_boxes_continue(self):
        load=self.insights.load_product
        def fail(client,sku):
            if sku=='002':raise ValueError('offline')
            load(client,sku)
        self.insights.load_product=fail
        self.service.run()
        state=self.service.status()
        self.assertEqual(state['salvas'],1)
        self.assertEqual(state['falhas'],1)
        self.assertEqual(state['status'],'com_erros')
        self.assertNotIn('ultimo_lote_completo_em',state)
        self.assertEqual([p['p_caixa'] for path,p in self.calls if path.startswith('rpc/')],['C2'])

    def test_403_stops_without_publishing_zeros(self):
        def fail(*args):
            error=HTTPError('https://example.invalid',403,'Denied',{},None);error.close();raise error
        self.insights.load_product=fail
        self.service.run()
        self.assertEqual(self.service.status()['salvas'],0)
        self.assertFalse(any(path.startswith('rpc/') for path,p in self.calls))
        self.assertEqual(self.service.status()['atual'],1)

    def test_duplicate_job_is_rejected(self):
        self.service.active=True
        with self.assertRaises(ValueError):self.service.start()

    def test_lock_requires_explicit_boolean_and_cloud_confirmation(self):
        pos={k:self.rows[0][k] for k in ('setor','prateleira','caixa')}
        with self.assertRaises(ValueError):self.service.set_lock(pos,'false')
        self.assertTrue(self.service.set_lock(pos,True)['bloqueada'])
        self.assertEqual(self.calls[-1][0],'rpc/definir_bloqueio_caixa')
        self.service.request=lambda *args:[]
        with self.assertRaises(ValueError):self.service.set_lock(pos,False)

    def test_interrupted_job_never_looks_active_after_restart(self):
        self.service.update(status='rodando',salvas=1)
        status=WarehouseControls(self.insights,self.service.request).status()
        self.assertEqual(status['status'],'interrompido')
        self.assertFalse(status['ativo'])

    def test_no_sales_logs_or_invoice_queries_in_stock_refresh(self):
        self.service.run()
        self.assertTrue(all('resumo_vendas' not in path and 'notas' not in path and 'logs-movimentacao' not in path for path,p in self.calls))
