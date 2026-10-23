from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from box_insights import BoxInsights
from box_occupancy import BoxOccupancy


class TestBoxOccupancy(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.insights=BoxInsights(Path(self.tmp.name)/'cache.db',lambda:None)
        self.started=datetime.now(timezone.utc)
        self.payloads=[]
        def request(path,payload):
            self.payloads.append((path,payload))
            return [{'sku':r['sku'],'unidades_estoque':r['unidades'],'unidades_atualizadas_em':payload['p_consultado_em']} for r in payload['p_itens']]
        self.service=BoxOccupancy(self.insights,request)
        self.position={'setor':'1S','prateleira':'P1','caixa':'C1'}

    def tearDown(self):self.tmp.cleanup()

    def product(self,sku,value,age=1):
        with self.insights.connect() as db:db.execute('INSERT OR REPLACE INTO products VALUES(?,?)',(sku,json.dumps({'sku':sku,'disponivel':value,'atualizado_em':(self.started+timedelta(seconds=age)).isoformat()})))

    def publish(self,skus):return self.service.publish(self.position,skus,self.started.isoformat())

    def test_publishes_only_stock_units_and_expected_position(self):
        self.product('A',1000000);self.product('B',3.5)
        result=self.publish(['A','B'])
        self.assertEqual(result['unidades'],1000003.5)
        path,payload=self.payloads[0]
        self.assertEqual(path,'rpc/atualizar_ocupacao_caixa')
        self.assertEqual(set(payload),{'p_setor','p_prateleira','p_caixa','p_itens','p_consultado_em'})
        self.assertEqual(set(payload['p_itens'][0]),{'sku','unidades'})

    def test_incomplete_or_stale_stock_never_publishes(self):
        self.product('A',4)
        with self.assertRaises(ValueError):self.publish(['A','B'])
        self.product('B',9,-1)
        with self.assertRaises(ValueError):self.publish(['A','B'])
        self.assertEqual(self.payloads,[])

    def test_missing_or_nonfinite_quantity_never_publishes(self):
        for n in (None,float('nan'),float('inf'),True):
            self.product('A',n)
            with self.assertRaises(ValueError):self.publish(['A'])
        self.assertEqual(self.payloads,[])

    def test_zero_is_valid_and_negative_available_stock_is_clamped(self):
        self.product('A',0);self.product('B',-4)
        self.assertEqual(self.publish(['A','B'])['unidades'],0)

    def test_unknown_or_partial_cloud_reply_is_not_confirmation(self):
        self.product('A',5)
        self.service.request=lambda *args:[]
        with self.assertRaises(ValueError):self.publish(['A'])

    def test_cloud_rejection_does_not_become_success(self):
        self.product('A',5)
        def reject(*args):raise ValueError('A composicao da caixa mudou')
        self.service.request=reject
        with self.assertRaisesRegex(ValueError,'composicao'):self.publish(['A'])

    def test_position_validation(self):
        for value in (None,{}, {'setor':'1S','prateleira':'P1','caixa':'C1&sku=neq.'}):
            with self.assertRaises(ValueError):self.service.position(value)
