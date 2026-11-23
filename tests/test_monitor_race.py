"""重连并发：停止标志与代际编号必须能中止过期任务。"""
import queue, threading, unittest

import steadylink as sl


class DummyCore:
    def __init__(self):
        self.token = 0
        self.running_flag = False
        self.calls = []

    def start(self, node, cfg, on_line=None):
        self.token += 1
        self.running_flag = True
        self.calls.append(('start', node.get('addr'), self.token))
        return self.token

    def stop(self, token=None):
        self.calls.append(('stop', token))
        if token is not None and token != self.token:
            return False
        self.running_flag = False
        return True

    def running(self):
        return self.running_flag

    def current_token(self):
        return self.token


class MonitorRaceTest(unittest.TestCase):
    def make_app(self, node):
        app = type('App', (), {})()
        app.core = DummyCore()
        app.cfg = {'http_port': 1, 'auto_failover': False, 'auto_reconnect': True}
        app.active_node = node
        app.conn_gen = 1
        app.conn_sess = None
        app.exit_ip = None
        app.q = queue.Queue()
        app.logs = []
        app.log = lambda m: app.logs.append(m)
        app._restart_access_tail = lambda: None
        app._apply_tz = lambda: None
        return app

    def test_uses_current_active_node(self):
        a = {'addr': 'A', 'link': 'la', 'remark': 'A'}
        b = {'addr': 'B', 'link': 'lb', 'remark': 'B'}
        app = self.make_app(a)
        stop = threading.Event()
        sl.App._attempt_recover(app, stop)
        starts_a = [c for c in app.core.calls if c[0] == 'start']
        self.assertTrue(starts_a and all(c[1] == 'A' for c in starts_a))

        app.core.calls.clear()
        app.active_node = b
        sl.App._attempt_recover(app, stop)
        starts_b = [c for c in app.core.calls if c[0] == 'start']
        self.assertTrue(starts_b and all(c[1] == 'B' for c in starts_b))

    def test_stop_flag_prevents_start(self):
        app = self.make_app({'addr': 'A', 'link': 'la', 'remark': 'A'})
        stop = threading.Event()
        stop.set()
        sl.App._attempt_recover(app, stop)
        self.assertEqual([c for c in app.core.calls if c[0] == 'start'], [])

    def test_failover_ignores_foreign_core(self):
        # 候选节点 start 返回 0（已被新连接接管）：不能调用任何 stop
        app = self.make_app({'addr': 'A', 'link': 'la', 'remark': 'A'})
        app.cfg['auto_failover'] = True
        app.nodes = [
            {'remark': 'B', 'link': 'vless://11111111-1111-1111-1111-111111111111@b.example.com:443?type=tcp&security=tls&sni=example.com#B', 'lat': 1},
            {'remark': 'C', 'link': 'vless://11111111-1111-1111-1111-111111111112@c.example.com:443?type=tcp&security=tls&sni=example.com#C', 'lat': 2},
        ]
        app.core.start = lambda node, cfg, on_line=None: (app.core.calls.append(('start', node.get('addr'), 0)), 0)[1]
        stop = threading.Event()
        sl.App._failover(app, stop)
        self.assertEqual([c for c in app.core.calls if c[0] == 'stop'], [])

    def test_failover_stops_own_token_only(self):
        app = self.make_app({'addr': 'A', 'link': 'la', 'remark': 'A'})
        app.cfg['auto_failover'] = True
        app.nodes = [
            {'remark': 'B', 'link': 'vless://11111111-1111-1111-1111-111111111111@b.example.com:443?type=tcp&security=tls&sni=example.com#B', 'lat': 1},
        ]

        def start_bumps_gen(node, cfg, on_line=None):
            app.core.token += 1
            app.core.calls.append(('start', node.get('addr'), app.core.token))
            app.conn_gen += 1  # 启动期间发生用户断开
            return app.core.token

        app.core.start = start_bumps_gen
        sl.App._failover(app, threading.Event())
        stops = [c for c in app.core.calls if c[0] == 'stop']
        self.assertTrue(stops)
        self.assertTrue(all(s[1] is not None for s in stops))

    def test_disconnect_during_start_aborts(self):
        app = self.make_app({'addr': 'A', 'link': 'la', 'remark': 'A'})
        stop = threading.Event()

        def start_and_bump(node, cfg, on_line=None):
            app.conn_gen += 1  # 模拟用户此时点了断开
            app.core.token += 1
            app.core.calls.append(('start', node.get('addr'), app.core.token))
            return app.core.token

        app.core.start = start_and_bump
        sl.App._attempt_recover(app, stop)
        self.assertTrue(any(c[0] == 'stop' for c in app.core.calls))
