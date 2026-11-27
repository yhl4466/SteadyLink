"""批量评分：并发、跳过无 IP、官方/本地模式、停止标志。"""
import threading
import unittest

import cleanip
import scoring


class BatchScoreTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = cleanip.full_check

        def fake(ip, proxy=None, key='', timeout=30):
            self.calls.append((ip, key, proxy))
            if ip == '9.9.9.9':
                return {'error': 'quota'}
            return {'source': 'cleanip' if key else 'local',
                    'score': 80, 'grade': 'B', 'ip_type': '住宅',
                    'flags': {'hosting': False}, 'note': ''}
        cleanip.full_check = fake

    def tearDown(self):
        cleanip.full_check = self._orig

    def _nodes(self):
        return [
            {'remark': 'A', 'ip': '1.1.1.1'},
            {'remark': 'B', 'ip': ''},
            {'remark': 'C', 'ip': '9.9.9.9'},
            {'remark': 'D', 'ip': '2.2.2.2'},
        ]

    def test_counts_and_fields(self):
        nodes = self._nodes()
        stats = scoring.score_nodes(nodes, workers=2)
        self.assertEqual(stats['total'], 3)
        self.assertEqual(stats['scored'], 2)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(nodes[0]['score'], 80)
        self.assertEqual(nodes[0]['grade'], 'B')
        self.assertEqual(nodes[0]['score_source'], 'local')
        self.assertIn('quota', nodes[2].get('score_note', ''))
        self.assertIsNone(nodes[1].get('score'))

    def test_official_passes_key(self):
        nodes = self._nodes()
        stats = scoring.score_nodes(nodes, official=True, key='cip_test', workers=1)
        self.assertEqual(stats['scored'], 2)
        self.assertTrue(all(k == 'cip_test' for _, k, _ in self.calls))
        self.assertEqual(nodes[0]['score_source'], 'cleanip')

    def test_progress_called(self):
        seen = []
        scoring.score_nodes(self._nodes(), workers=2,
                            progress=lambda d, t, n, r: seen.append((d, t)))
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[-1][0], seen[-1][1])

    def test_stop_prevents_new_work(self):
        nodes = [{'remark': str(i), 'ip': f'10.0.0.{i}'} for i in range(6)]
        stop = threading.Event()
        stop.set()
        stats = scoring.score_nodes(nodes, workers=2, stop=stop)
        self.assertTrue(stats['stopped'])
        self.assertEqual(stats['scored'], 0)
