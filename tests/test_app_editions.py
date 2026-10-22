import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_edition import load_edition
from box_insights import BoxInsights
from shared_sales import SharedSales


class TestEditions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.insights = BoxInsights(self.root/'cache.db', lambda: self.fail('Tiny nao deve ser acessado'))

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, edition):
        return SharedSales(self.insights, 'https://example.invalid', 'public', self.root, edition)

    def test_reader_ignores_local_and_environment_publication_keys(self):
        (self.root/'vendas_publicador.json').write_text(json.dumps({'chave':'local-key'}))
        with patch.dict('os.environ', {'HL_VENDAS_PUBLICADOR':'environment-key'}):
            service = self.service({'edicao':'leitor','chave_publicador':'must-ignore'})
            self.assertEqual(service.publisher_key(),'')
            self.assertFalse(service.status()['publicador'])

    def test_publisher_uses_its_bundled_key_not_old_other_pc_configuration(self):
        (self.root/'vendas_publicador.json').write_text(json.dumps({'chave':'old-local-key'}))
        with patch.dict('os.environ', {'HL_VENDAS_PUBLICADOR':'old-environment-key'}):
            service = self.service({'edicao':'publicador','chave_publicador':'bundled-key'})
            self.assertEqual(service.publisher_key(),'bundled-key')
            self.assertTrue(service.status()['publicador'])

    def test_broken_profile_fails_closed(self):
        (self.root/'hl_edicao.json').write_text('{not-json')
        self.assertEqual(load_edition(self.root),{'edicao':'leitor'})

    def test_missing_profile_preserves_legacy_configuration(self):
        self.assertEqual(load_edition(self.root),{'edicao':'padrao'})

    def test_profile_loads_publisher(self):
        profile = {'edicao':'publicador','chave_publicador':'bundled-key'}
        (self.root/'hl_edicao.json').write_text(json.dumps(profile))
        self.assertEqual(load_edition(self.root),profile)


if __name__=='__main__':
    unittest.main()
