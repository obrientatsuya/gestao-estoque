import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import test_box_insights as fixtures
from box_insights import BoxInsights, ImportLimiter


class FastLimiter:
    def __init__(self, stop):
        self.stop = stop

    def aguardar(self):
        pass

    def quota(self, headers):
        pass


class TestInvoiceImport(unittest.TestCase):
    complete = fixtures.TestInsights.complete

    def setUp(self):
        fixtures.TestInsights.setUp(self)
        self.patcher = patch('box_insights.ImportLimiter', FastLimiter)
        self.patcher.start()

    def tearDown(self):
        self.cache.pause_import()
        self.wait_done()
        self.patcher.stop()
        fixtures.TestInsights.tearDown(self)

    def wait_done(self):
        deadline = time.monotonic() + 5
        while self.cache.import_status()['ativo'] and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.cache.import_status()['ativo'])

    def test_full_import_get_only_and_restart_idle(self):
        self.cache.start_import()
        self.wait_done()
        status = self.cache.import_status()
        self.assertEqual(status['status'], 'concluido')
        self.assertEqual(status['percentual'], 100)
        self.assertEqual(status['notas'], 7)
        self.assertEqual(self.cache.cache(['001'])['itens'][0]['vendas'], 2)
        paths = [p for p in self.client.calls if p.startswith('/notas?')]
        self.assertEqual(sorted(int(parse_qs(urlparse(p).query)['offset'][0]) for p in paths), [0, 2, 4, 6])
        with self.cache.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM invoice_sales').fetchone()[0], 4)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        before = len(self.client.calls)
        other = BoxInsights(self.cache.path, lambda: self.client)
        self.assertFalse(other.import_status()['ativo'])
        self.assertEqual(other.import_status()['status'], 'concluido')
        self.assertEqual(len(self.client.calls), before)

    def test_pause_resume_preserves_cursor_no_automatic_restart(self):
        original = self.client.request_json
        entered, release = threading.Event(), threading.Event()

        def blocked(method, path):
            if '/notas?' in path and int(parse_qs(urlparse(path).query)['offset'][0]) >= 2:
                entered.set()
                release.wait(3)
            return original(method, path)

        self.client.request_json = blocked
        self.cache.start_import()
        self.assertTrue(entered.wait(3))
        with self.assertRaises(ValueError):
            self.cache.start_import()
        self.cache.pause_import()
        release.set()
        self.wait_done()
        self.assertEqual(self.cache.get_state()['offset'], 2)
        before = len(self.client.calls)
        self.cache = BoxInsights(self.cache.path, lambda: self.client)
        self.assertEqual(self.cache.import_status()['status'], 'pausado')
        self.assertEqual(len(self.client.calls), before)
        self.client.request_json = original
        self.cache.start_import()
        self.wait_done()
        self.assertEqual(self.cache.import_status()['status'], 'concluido')
        self.assertEqual(self.cache.cache(['001'])['itens'][0]['unidades_vendidas'], 5)

    def test_later_page_failure_does_not_skip_gap_or_destroy_previous_cache(self):
        self.complete()
        old_generation = self.cache.get_state()['active_generation']
        original = self.client.request_json

        def fail(method, path):
            if '/notas?' in path and parse_qs(urlparse(path).query)['offset'] == ['2']:
                raise RuntimeError('HTTP 403: indisponivel')
            return original(method, path)

        self.client.request_json = fail
        self.cache.start_import()
        self.wait_done()
        self.assertEqual(self.cache.import_status()['status'], 'erro')
        self.assertEqual(self.cache.get_state()['offset'], 2)
        self.assertEqual(self.cache.get_state()['active_generation'], old_generation)
        self.assertEqual(self.cache.cache(['001'])['itens'][0]['vendas'], 2)
        self.client.request_json = original
        self.cache.start_import()
        self.wait_done()
        self.assertNotEqual(self.cache.get_state()['active_generation'], old_generation)

    def test_bulk_does_not_block_box_cache_or_start_second_page_scan(self):
        entered, release = threading.Event(), threading.Event()
        original = self.client.request_json

        def blocked(method, path):
            if '/notas?' in path:
                entered.set()
                release.wait(3)
            return original(method, path)

        self.client.request_json = blocked
        self.cache.start_import()
        self.assertTrue(entered.wait(3))
        before = len(self.client.calls)
        self.cache.invoice_page(self.client)
        self.cache.cache(['001'])
        self.assertEqual(len(self.client.calls), before)
        self.cache.pause_import()
        release.set()
        self.wait_done()

    def test_429_retry_is_interruptible_and_keeps_cursor(self):
        entered = threading.Event()

        def limited(method, path):
            self.assertEqual(method, 'GET')
            entered.set()
            error = HTTPError(path, 429, 'quota', {'Retry-After': '60'}, None)
            error.close()
            raise error

        self.client.request_json = limited
        self.cache.start_import()
        self.assertTrue(entered.wait(3))
        self.cache.pause_import()
        self.wait_done()
        self.assertEqual(self.cache.get_state()['offset'], 0)
        self.assertEqual(self.cache.import_status()['status'], 'pausado')

    def test_stress_2500_invoices_25000_lines_two_requests_max(self):
        mutex = threading.Lock()
        activity = {'active': 0, 'peak': 0, 'calls': 0}

        def pages(method, path):
            self.assertEqual(method, 'GET')
            self.assertEqual(urlparse(path).path, '/notas')
            offset = int(parse_qs(urlparse(path).query)['offset'][0])
            with mutex:
                activity['active'] += 1
                activity['peak'] = max(activity['peak'], activity['active'])
                activity['calls'] += 1
            time.sleep(.02 if offset % 200 == 100 else .005)
            notes = [{'id': i, 'tipo': 'S', 'situacao': '7', 'dataEmissao': '2026-10-01',
                      'itens': [fixtures.line(10, '001', line_id=j) for j in range(10)]}
                     for i in range(offset, min(offset + 100, 2500))]
            with mutex:
                activity['active'] -= 1
            return {'body': {'itens': notes, 'paginacao': {'offset': offset, 'limit': 100, 'total': 2500}}}

        self.client.request_json = pages
        self.cache.start_import()
        self.wait_done()
        self.assertEqual(self.cache.import_status()['status'], 'concluido')
        self.assertEqual(activity['peak'], 2)
        self.assertEqual(activity['calls'], 25)
        item = self.cache.cache(['001'])['itens'][0]
        self.assertEqual(item['vendas'], 2500)
        self.assertEqual(item['unidades_vendidas'], 25000)
        with self.cache.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM invoice_sales').fetchone()[0], 25000)


class TestLimiter(unittest.TestCase):
    def test_quota_never_uses_more_than_half_or_sixty(self):
        limiter = ImportLimiter(threading.Event())
        self.assertEqual(limiter.interval, 2)
        limiter.quota({'x-ratelimit-limit': '120'})
        self.assertEqual(limiter.interval, 1)
        limiter.quota({'x-ratelimit-limit': '60'})
        self.assertEqual(limiter.interval, 2)
        limiter.quota({'x-ratelimit-limit': '1000'})
        self.assertEqual(limiter.interval, 1)
        limiter.quota({'x-ratelimit-limit': 'invalid'})
        self.assertEqual(limiter.interval, 1)


if __name__ == '__main__':
    unittest.main()
