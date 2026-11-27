"""CleanIP MCP 客户端：响应解析与回退逻辑（不联网）。"""
import unittest

import cleanip
import ipscore


class ParseTest(unittest.TestCase):
    def test_parse_plain_json(self):
        self.assertEqual(cleanip._parse_body('{"a": 1}'), {'a': 1})

    def test_parse_sse(self):
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
        self.assertEqual(cleanip._parse_body(body),
                         {'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}})

    def test_parse_empty(self):
        self.assertIsNone(cleanip._parse_body(''))
        self.assertIsNone(cleanip._parse_body('garbage'))


class NormalizeTest(unittest.TestCase):
    def test_official_fields(self):
        data = {
            'cleanip_score': 81, 'cleanip_grade': 'A', 'ip_type': 'residential',
            'native_or_broadcast': 'native',
            'flags': {'proxy': False, 'vpn': False, 'tor': False, 'hosting': False},
            'findings': [{'code': 'idc', 'desc': '机房', 'delta': -5}],
            'scenarios': {'AI 服务': {'score': 78}},
            'location': {'country': '英国', 'country_code': 'GB'},
            'network': {'asn': 204044, 'as_name': 'Packet Star'},
            'report_url': 'https://cleanip.io/185.90.62.114',
        }
        r = cleanip.normalize(data)
        self.assertEqual(r['source'], 'cleanip')
        self.assertEqual(r['score'], 81)
        self.assertEqual(r['grade'], 'A')
        self.assertTrue(r['native'])
        self.assertFalse(r['broadcast'])
        self.assertEqual(r['ip_type'], 'residential')
        self.assertEqual(len(r['findings']), 1)
        self.assertIn('AI 服务', r['scenarios'])

    def test_alternative_names(self):
        data = {'score': 40, 'grade': 'D', 'is_proxy': True, 'is_hosting': True,
                'broadcast': True}
        r = cleanip.normalize(data)
        self.assertEqual(r['score'], 40)
        self.assertEqual(r['grade'], 'D')
        self.assertTrue(r['flags']['proxy'])
        self.assertTrue(r['flags']['hosting'])
        self.assertTrue(r['broadcast'])


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self._orig_check = cleanip.check_ip
        self._orig_local = ipscore.check
        self._orig_rpc = cleanip.rpc

    def tearDown(self):
        cleanip.check_ip = self._orig_check
        ipscore.check = self._orig_local
        cleanip.rpc = self._orig_rpc

    def test_no_key_uses_local(self):
        sentinel = {'source': 'local', 'score': 77, 'grade': 'B'}
        ipscore.check = lambda ip, proxy=None: dict(sentinel)
        r = cleanip.full_check('1.2.3.4', key='')
        self.assertEqual(r['score'], 77)
        self.assertIn('本地估算', r.get('note', ''))

    def test_official_success(self):
        cleanip.check_ip = lambda ip, key='', proxy=None, timeout=30: {
            'cleanip_score': 88, 'cleanip_grade': 'A', 'ip_type': 'residential'}
        r = cleanip.full_check('1.2.3.4', key='cip_test')
        self.assertEqual(r['source'], 'cleanip')
        self.assertEqual(r['score'], 88)

    def test_official_error_falls_back(self):
        cleanip.check_ip = lambda ip, key='', proxy=None, timeout=30: {'error': 'quota'}
        ipscore.check = lambda ip, proxy=None: {'source': 'local', 'score': 70, 'grade': 'B'}
        r = cleanip.full_check('1.2.3.4', key='cip_test')
        self.assertEqual(r['score'], 70)
        self.assertIn('本地估算', r.get('note', ''))
