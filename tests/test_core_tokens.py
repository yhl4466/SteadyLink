"""Core 令牌：过期线程不能停掉新连接的进程。"""
import unittest

from .support import TempProfile, fake_node, HAS_XRAY


@unittest.skipUnless(HAS_XRAY, '缺少 bin/xray.exe')
class CoreTokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = TempProfile()

    @classmethod
    def tearDownClass(cls):
        cls.profile.close()

    def setUp(self):
        self.core = __import__('v2core').Core()
        self.cfg = self.profile.config()

    def tearDown(self):
        try:
            self.core.stop()
        except Exception:
            pass

    def test_stale_token_cannot_stop_new_core(self):
        t1 = self.core.start(fake_node(1), self.cfg)
        self.assertGreater(t1, 0)
        self.assertTrue(self.core.running())

        t2 = self.core.start(fake_node(2), self.cfg)
        self.assertGreater(t2, t1)
        self.assertTrue(self.core.running())

        # 旧线程拿着旧 token 想停核心：必须失败，且新核心仍在运行
        self.assertFalse(self.core.stop(t1))
        self.assertTrue(self.core.running())

        self.assertTrue(self.core.stop())
        self.assertFalse(self.core.running())

    def test_invalid_node_returns_zero(self):
        bad = {'ct': 99, 'addr': 'x', 'port': 1}
        self.assertEqual(self.core.start(bad, self.cfg), 0)
        self.assertFalse(self.core.running())

    def test_start_failure_leaves_no_process(self):
        import v2core
        old = v2core.CORE
        v2core.CORE = r'C:\no\such\xray.exe'
        try:
            self.assertEqual(self.core.start(fake_node(1), self.cfg), 0)
            self.assertIsNone(self.core.proc)
        finally:
            v2core.CORE = old
