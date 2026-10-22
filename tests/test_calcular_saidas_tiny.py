import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calcular_saidas_tiny import calculate, fetch_movements, main, read_file


class TestCalculate(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'id':1, 'tipo':'S', 'valor':'38,22', 'quantidade':2},
            {'id':2, 'tipo':'S', 'valor':'0,00', 'quantidade':8},
            {'id':3, 'tipo':'E', 'valor':50, 'quantidade':4},
            {'id':4, 'tipo':'B', 'valor':50, 'quantidade':4},
            {'id':5, 'tipo':'S', 'valor':'12,50', 'quantidade':'3,00'},
            {'id':6, 'tipo':'S', 'valor':-1, 'quantidade':1}]

    def test_local_count_and_deduplication(self):
        result = calculate(self.rows + [self.rows[0]])
        self.assertEqual(result['vendas'], 2)
        self.assertEqual(result['unidades_vendidas'], 5)
        self.assertEqual(result['registros_ignorados'], 4)

    def test_decimal_quantities_and_zero_quantity(self):
        result = calculate([{'id':i, 'tipo':'S', 'valor':1, 'quantidade':q}
                            for i,q in enumerate(['0,1','0,2','0,0'])])
        self.assertEqual(result['unidades_vendidas'], 0.3)
        self.assertEqual(result['vendas'], 2)

    def test_missing_and_conflicting_data_fail_closed(self):
        with self.assertRaises(ValueError):
            calculate([{'id':1, 'tipo':'S', 'quantidade':1}])
        with self.assertRaises(ValueError):
            calculate([self.rows[0], {**self.rows[0], 'quantidade':8}])
        with self.assertRaises(ValueError):
            calculate(['invalido'])

    def test_fetch_all_types_and_all_pages(self):
        rows = self.rows
        class Fake:
            def request_json(self, method, path):
                from urllib.parse import urlparse,parse_qs
                self_path = urlparse(path)
                query = parse_qs(self_path.query)
                assert method == 'GET'
                assert self_path.path == '/estoque/123/logs-movimentacao'
                assert 'tipo' not in query
                offset = int(query['offset'][0])
                return {'body':{'itens':rows[offset:offset+2], 'limit':2, 'total':len(rows)}}
        self.assertEqual(calculate(fetch_movements(Fake(), 123, '2000-01-01', '2026-10-21'))['vendas'], 2)

    def test_incomplete_page_and_403_do_not_produce_counts(self):
        class Empty:
            def request_json(self,*args):
                return {'body':{'itens':[], 'total':10}}
        class Forbidden:
            def request_json(self,*args):
                raise RuntimeError('HTTP 403')
        for client in (Empty(), Forbidden()):
            with self.assertRaises((ValueError,RuntimeError)):
                fetch_movements(client,123,'2000-01-01','2026-10-21')

    def test_cli_json_input_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'movimentos.json'
            target = Path(folder)/'resultado.json'
            source.write_text(json.dumps({'itens':self.rows,'total':6}), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--arquivo',str(source),'--saida',str(target)]), 0)
            report = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(report['vendas'],2)
            self.assertEqual(report['unidades_vendidas'],5)
            source.write_text(json.dumps({'itens':self.rows,'total':20}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'parte'):
                read_file(source)
            source.write_text(json.dumps({'status':403,'body':None}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'403'):
                read_file(source)


if __name__ == '__main__':
    unittest.main()
