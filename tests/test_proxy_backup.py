"""系统代理备份：结构校验、拒绝接管、崩溃遗留恢复。"""
import json, os, sys, unittest

from .support import TempProfile

IS_WIN = sys.platform == 'win32'


@unittest.skipUnless(IS_WIN, '仅 Windows')
class ProxyBackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = TempProfile()
        import v2core
        cls.core = v2core

    @classmethod
    def tearDownClass(cls):
        cls.profile.close()

    def setUp(self):
        self.original = self.core.read_proxy_registry()
        # 清空备份文件，保证每个用例独立
        if os.path.exists(self.core.PROXY_BACKUP):
            os.unlink(self.core.PROXY_BACKUP)

    def tearDown(self):
        # 无论用例成败，都恢复用户原始代理设置
        try:
            if self.original:
                self.core._write_proxy_registry(self.original)
        finally:
            if os.path.exists(self.core.PROXY_BACKUP):
                os.unlink(self.core.PROXY_BACKUP)

    def test_valid_structure(self):
        good = {'ProxyEnable': {'value': 0, 'type': 4},
                'ProxyServer': None, 'ProxyOverride': None, 'AutoConfigURL': None}
        self.assertTrue(self.core._valid_proxy_backup(good))
        self.assertFalse(self.core._valid_proxy_backup(None))
        self.assertFalse(self.core._valid_proxy_backup({'junk': 1}))
        self.assertFalse(self.core._valid_proxy_backup(
            {'ProxyEnable': {'value': 0, 'type': 4}}))
        self.assertFalse(self.core._valid_proxy_backup(
            {'ProxyEnable': {'value': 0, 'type': 999}, 'ProxyServer': None,
             'ProxyOverride': None, 'AutoConfigURL': None}))

    def test_refuse_takeover_on_corrupt_backup(self):
        json.dump({'junk': 'broken'}, open(self.core.PROXY_BACKUP, 'w', encoding='utf-8'))
        before = self.core.read_proxy_registry()
        self.assertFalse(self.core.set_system_proxy(23456))
        self.assertEqual(before, self.core.read_proxy_registry())

    def test_save_and_restore_roundtrip(self):
        before = self.core.read_proxy_registry()
        self.assertTrue(self.core.set_system_proxy(23456))
        mid = self.core.read_proxy_registry()
        self.assertEqual(mid['ProxyEnable']['value'], 1)
        self.assertIn('23456', str(mid['ProxyServer']['value']))
        self.assertTrue(self.core.set_system_proxy(None))
        self.assertEqual(before, self.core.read_proxy_registry())
        self.assertFalse(os.path.exists(self.core.PROXY_BACKUP))

    def test_crash_leftover_recovery(self):
        before = self.core.read_proxy_registry()
        self.core.save_proxy_state(force=True)
        with self.core.winreg.OpenKey(self.core.winreg.HKEY_CURRENT_USER,
                                      self.core.KEY, 0, self.core.winreg.KEY_SET_VALUE) as k:
            self.core.winreg.SetValueEx(k, 'ProxyServer', 0, self.core.winreg.REG_SZ,
                                        '127.0.0.1:20901')
            self.core.winreg.SetValueEx(k, 'ProxyEnable', 0, self.core.winreg.REG_DWORD, 1)
        self.core._proxy_refresh()
        self.assertTrue(self.core.recover_proxy_if_needed((20901,)))
        self.assertEqual(before, self.core.read_proxy_registry())
        self.assertFalse(os.path.exists(self.core.PROXY_BACKUP))
