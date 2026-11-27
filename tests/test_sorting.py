"""主表排序选项与评分显示。"""
import unittest

import steadylink


class SortingTest(unittest.TestCase):
    def test_sort_choices(self):
        self.assertEqual(steadylink.SORT_CHOICES['评分'], ('score', True))
        self.assertEqual(steadylink.SORT_CHOICES['国家'], ('loc', False))
        self.assertEqual(steadylink.SORT_CHOICES['名称'], ('remark', False))

    def test_score_text(self):
        self.assertEqual(steadylink.score_text({'score': 81, 'grade': 'A'}), '81 A')
        self.assertEqual(steadylink.score_text({'score': None}), '—')
        self.assertEqual(steadylink.score_text({'score': 30}), '30')


if __name__ == '__main__':
    unittest.main()
