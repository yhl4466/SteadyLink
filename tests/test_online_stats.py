"""在线时长口径：只累计“两次检查都正常”的区间。"""
import time, unittest

import steadylink as sl


class OnlineStatsTest(unittest.TestCase):
    def setUp(self):
        self.acct = sl.App._account_check

    def test_interval_accounting(self):
        sess = {'online': 0.0, 'failures': 0, 'check_ok': False, 'check_time': None}
        t = 1000.0
        self.acct(sess, True, t)          # 起点，不累计
        self.acct(sess, True, t + 30)     # 两次正常 -> +30
        self.acct(sess, False, t + 60)    # 故障 -> 不加，区间结束
        self.acct(sess, True, t + 90)     # 恢复 -> 新起点
        self.assertAlmostEqual(sess['online'], 30.0, places=1)
        self.assertEqual(sess['failures'], 1)

    def test_close_adds_last_confirmed_interval(self):
        class Dummy:
            pass
        d = Dummy()
        now = time.time()
        d.conn_sess = {'node': 'n', 'link': 'l', 'start': now - 100,
                       'online': 30.0, 'check_ok': True, 'check_time': now - 5,
                       'failures': 1, 'reconnects': 1, 'failovers': 0,
                       'recover_times': [3.0], 'ip_changes': 0}
        d.conn_hist = []
        old_save = sl.save_conn_hist
        sl.save_conn_hist = lambda hist: None
        try:
            sl.App._close_conn_session(d)
        finally:
            sl.save_conn_hist = old_save
        rec = d.conn_hist[-1]
        self.assertAlmostEqual(rec['online'], 35.0, delta=1.0)
        self.assertEqual(rec['failures'], 1)
        self.assertEqual(rec['reconnects'], 1)

    def test_close_when_offline_does_not_add(self):
        class Dummy:
            pass
        d = Dummy()
        now = time.time()
        d.conn_sess = {'node': 'n', 'link': 'l', 'start': now - 100,
                       'online': 30.0, 'check_ok': False, 'check_time': now - 10,
                       'failures': 3, 'reconnects': 0, 'failovers': 0,
                       'recover_times': [], 'ip_changes': 0}
        d.conn_hist = []
        old_save = sl.save_conn_hist
        sl.save_conn_hist = lambda hist: None
        try:
            sl.App._close_conn_session(d)
        finally:
            sl.save_conn_hist = old_save
        self.assertAlmostEqual(d.conn_hist[-1]['online'], 30.0, places=1)
