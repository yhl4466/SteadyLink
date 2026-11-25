"""迷你 v2rayN —— 自制节点管理器（Tkinter GUI）。

功能：多节点管理 / 导入+更新订阅 / 真实测速 / 按列排序 / 搜索过滤 / 一键连接 /
系统代理 / 出口 IP 监控 / 用量统计 / 右键菜单 / 自动更新订阅。

设计原则：默认固定节点、不自动切换（防 Claude 风控）；自动故障切换需手动开启。
"""
import ctypes, os, queue, re, subprocess, sys, threading, time, tkinter as tk
from tkinter import messagebox, simpledialog, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v2core as core
import downloader as dl

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    TRAY_OK = True
except Exception:
    TRAY_OK = False

APP_NAME = 'SteadyLink'
ICON_ICO = os.path.join(HERE, 'assets', 'icon.ico')
ICON_PNG = os.path.join(HERE, 'assets', 'icon.png')
TRAY_PNG = os.path.join(HERE, 'assets', 'tray.png')


def startup_cmd_path():
    base = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                        'Start Menu', 'Programs', 'Startup')
    return os.path.join(base, APP_NAME + '.cmd')


def autostart_enabled():
    return os.path.exists(startup_cmd_path())


def set_autostart(on):
    path = startup_cmd_path()
    try:
        if on:
            pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
            if not os.path.exists(pyw):
                pyw = sys.executable
            content = ('@echo off\r\ncd /d "%s"\r\nstart "" "%s" steadylink.py\r\nexit\r\n'
                       % (HERE, pyw))
            try:
                enc = 'mbcs'  # 中文 Windows 下的 ANSI，cmd 能正确识别中文路径
            except Exception:
                enc = 'utf-8'
            with open(path, 'wb') as f:
                f.write(content.encode(enc, 'replace'))
        elif os.path.exists(path):
            os.unlink(path)
        return True
    except Exception:
        return False

REGION_MAP = [('United States', '美国'), ('Japan', '日本'), ('Korea', '韩国'),
              ('Singapore', '新加坡'), ('United Kingdom', '英国'), ('Germany', '德国'),
              ('Hong Kong', '香港'), ('Taiwan', '台湾'), ('Malaysia', '马来西亚'),
              ('Netherlands', '荷兰'), ('Switzerland', '瑞士'), ('France', '法国')]
INFO_WORDS = ('剩余流量', '距离下次', '套餐到期', '官网')
CONN_HIST_FILE = os.path.join(HERE, '连接历史.json')


def load_conn_hist():
    try:
        import json as _json
        data = _json.load(open(CONN_HIST_FILE, encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_conn_hist(hist):
    import json as _json
    try:
        tmp = CONN_HIST_FILE + '.tmp'
        _json.dump(hist, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, CONN_HIST_FILE)
    except Exception:
        pass


def pretty_remark(rem):
    s = re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]', '', rem or '').strip()
    for en, zh in REGION_MAP:
        s = s.replace(en, zh)
    return re.sub(r'\s{2,}', ' ', s).strip() or (rem or '')


BG = '#f3f6fb'
CARD = '#ffffff'
BORDER = '#e3e9f2'
ACCENT = '#2563eb'
ACCENT_DARK = '#1d4ed8'
TEXT = '#1f2937'
MUTED = '#64748b'
OKC = '#16a34a'
WARNC = '#d97706'
ERRC = '#dc2626'

COLS = ('remark', 'addr', 'port', 'proto', 'lat', 'ip', 'loc', 'state')
HEADS = {'remark': '别名', 'addr': '地址', 'port': '端口', 'proto': '协议',
         'lat': '延迟', 'ip': '出口IP', 'loc': '地区', 'state': '状态'}
WIDTHS = {'remark': 210, 'addr': 210, 'port': 65, 'proto': 70,
          'lat': 85, 'ip': 145, 'loc': 55, 'state': 95}
SORTABLE = ('remark', 'addr', 'port', 'proto', 'lat', 'ip', 'loc', 'state')


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = core.load_config()
        self.nodes = core.load_nodes()
        for n in self.nodes:
            n['remark'] = pretty_remark(n.get('remark', ''))
            n.setdefault('source', 'manual')
        self.core = core.Core()
        self.usage = core.Usage()
        self.conn_sess = None
        self.conn_hist = load_conn_hist()
        self.active_node = None
        self.conn_gen = 0
        self.q = queue.Queue()
        self.connected_link = None
        self.exit_ip = None
        self.monitor_stop = threading.Event()
        self.monitor_thread = None
        self.testing = False
        self.updating = False
        self.log_lines = []
        self.paused = False
        self.log_win = None
        self.log_win_text = None
        self._tail_stop = threading.Event()
        self.sort_col = 'lat'
        self.sort_desc = False
        self._quitting = False
        self.tray = None
        self.region_var = tk.StringVar(value='全部')
        self.filter_text = tk.StringVar()
        self.filter_text.trace_add('write', lambda *a: self.refresh())
        self.count_var = tk.StringVar(value='')
        self.status = tk.StringVar(value='● 未连接')

        root.title('SteadyLink — 节点管理器')
        root.geometry('1360x900')
        root.minsize(1000, 640)
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._set_window_icon()
        self.root.after(250, self._set_window_icon)
        self._style()
        self._build()
        self._maybe_first_import()
        self.refresh()
        self._setup_tray()
        self._poll_queue()
        self._tick()
        self.root.after(600, self._proxy_recovery_check)

    def _set_window_icon(self):
        """Windows 标题栏/任务栏图标：优先多尺寸 ico，非 Windows 用 png。"""
        try:
            if os.path.exists(ICON_ICO):
                self.root.iconbitmap(ICON_ICO)
                self.root.iconbitmap(default=ICON_ICO)
            elif sys.platform != 'win32' and os.path.exists(ICON_PNG):
                self._icon_img = tk.PhotoImage(file=ICON_PNG)
                self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass

    # ---------------- UI ----------------
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use('clam')
        except Exception:
            pass
        st.configure('.', font=('Microsoft YaHei UI', 10), background=BG, foreground=TEXT)
        st.configure('TFrame', background=BG)
        st.configure('Card.TFrame', background=CARD)
        st.configure('TLabel', background=BG, foreground=TEXT)
        st.configure('Card.TLabel', background=CARD, foreground=TEXT)
        st.configure('Muted.TLabel', foreground=MUTED, font=('Microsoft YaHei UI', 9))
        st.configure('H1.TLabel', font=('Microsoft YaHei UI', 17, 'bold'), foreground=TEXT)
        st.configure('H2.TLabel', font=('Microsoft YaHei UI', 12, 'bold'), foreground=TEXT)
        st.configure('TButton', padding=(12, 6), background='#eef2f9', foreground=TEXT,
                     borderwidth=0, focusthickness=0, relief='flat',
                     font=('Microsoft YaHei UI', 10))
        st.map('TButton', background=[('active', '#e0e7f3'), ('pressed', '#cfd9e8')])
        st.configure('Ghost.TButton', background=CARD, padding=(10, 6), foreground=TEXT,
                     borderwidth=0, focusthickness=0, relief='flat')
        st.map('Ghost.TButton', background=[('active', '#eef2f9'), ('pressed', '#e0e7f3')])
        st.configure('Primary.TButton', background=ACCENT, foreground='white',
                     font=('Microsoft YaHei UI', 10, 'bold'), padding=(16, 7))
        st.map('Primary.TButton', background=[('active', ACCENT_DARK), ('pressed', '#1e40af')])
        st.configure('Danger.TButton', background='#fff1f2', foreground=ERRC)
        st.map('Danger.TButton', background=[('active', '#ffe4e6')])
        st.configure('TCheckbutton', background=BG, foreground=TEXT,
                     font=('Microsoft YaHei UI', 9))
        st.configure('Card.TCheckbutton', background=CARD, foreground=TEXT,
                     font=('Microsoft YaHei UI', 9))
        st.map('TCheckbutton', background=[('active', BG)])
        st.map('Card.TCheckbutton', background=[('active', CARD)])
        st.configure('TEntry', fieldbackground='white', padding=5)
        st.configure('TCombobox', padding=4)
        st.configure('TSeparator', background=BORDER)
        st.configure('Treeview', rowheight=32, font=('Microsoft YaHei UI', 10),
                     background=CARD, fieldbackground=CARD, borderwidth=0)
        st.configure('Treeview.Heading', font=('Microsoft YaHei UI', 9, 'bold'),
                     background='#f1f5fb', foreground=MUTED, relief='flat', padding=(8, 7))
        st.map('Treeview', background=[('selected', '#dbeafe')],
               foreground=[('selected', '#0f172a')])
        st.map('Treeview.Heading', background=[('active', '#e8eef8')])
        st.configure('TScrollbar', background='#e8edf5', arrowcolor=MUTED,
                     borderwidth=0, troughcolor='#f7f9fd')
        self._setup_check_images(st)
        st.configure('TNotebook', background=BG, borderwidth=0)
        st.configure('TNotebook.Tab', padding=(14, 6), font=('Microsoft YaHei UI', 9))
        st.map('TNotebook.Tab', background=[('selected', CARD)])

    def _setup_check_images(self, st):
        """用自绘图片替换默认的方框+叉，统一成圆角勾选样式。"""
        try:
            from PIL import Image, ImageDraw, ImageTk
            def make(kind):
                im = Image.new('RGBA', (18, 18), (0, 0, 0, 0))
                d = ImageDraw.Draw(im)
                if kind == 'off':
                    d.rounded_rectangle([1, 1, 16, 16], radius=5, fill='white',
                                        outline='#cbd5e1', width=2)
                elif kind == 'hover':
                    d.rounded_rectangle([1, 1, 16, 16], radius=5, fill='#f8fafc',
                                        outline=ACCENT, width=2)
                else:
                    d.rounded_rectangle([1, 1, 16, 16], radius=5, fill=ACCENT,
                                        outline=ACCENT, width=2)
                    d.line([(4.5, 9), (7.5, 12), (13, 5.5)], fill='white', width=2)
                return ImageTk.PhotoImage(im)
            self._ck_imgs = {k: make(k) for k in ('off', 'hover', 'on')}
            st.element_create('Steady.Ck', 'image', self._ck_imgs['off'],
                              ('selected', self._ck_imgs['on']),
                              ('active', self._ck_imgs['hover']),
                              ('disabled', self._ck_imgs['off']))
            for name in ('TCheckbutton', 'Card.TCheckbutton'):
                st.layout(name, [('Checkbutton.padding', {
                    'sticky': 'nswe', 'children': [
                        ('Steady.Ck', {'side': 'left', 'sticky': ''}),
                        ('Checkbutton.label', {'sticky': 'nswe'}),
                    ]})])
        except Exception:
            pass

    def _build(self):
        # ---- 标题栏 ----
        header = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        header.pack(fill='x')
        inner = tk.Frame(header, bg=CARD, padx=18, pady=12)
        inner.pack(fill='x')
        try:
            from PIL import Image, ImageTk
            img = Image.open(ICON_PNG).resize((38, 38), Image.LANCZOS)
            self._header_icon = ImageTk.PhotoImage(img)
            tk.Label(inner, image=self._header_icon, bg=CARD).pack(side='left', padx=(0, 12))
        except Exception:
            pass
        title_box = tk.Frame(inner, bg=CARD)
        title_box.pack(side='left')
        tk.Label(title_box, text='SteadyLink', bg=CARD, fg=TEXT,
                 font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w')
        tk.Label(title_box, text='节点管理器 · 固定 IP · 防封号', bg=CARD, fg=MUTED,
                 font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(1, 0))
        self.status_pill = tk.Label(inner, text='● 未连接', bg='#94a3b8', fg='white',
                                    font=('Microsoft YaHei UI', 9, 'bold'), padx=16, pady=5)
        self.status_pill.pack(side='right')

        # ---- 工具栏 ----
        bar = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill='x', padx=10, pady=(10, 0))
        b = tk.Frame(bar, bg=CARD, padx=12, pady=9)
        b.pack(fill='x')
        self.btn_update = ttk.Button(b, text='⟳ 更新订阅', command=self.update_sub)
        self.btn_update.pack(side='left')
        ttk.Button(b, text='＋ 导入订阅', command=self.import_sub).pack(side='left', padx=(6, 0))
        ttk.Button(b, text='从剪贴板导入', command=self.import_clipboard).pack(side='left', padx=6)
        ttk.Separator(b, orient='vertical').pack(side='left', fill='y', padx=8)
        self.btn_test_sel = ttk.Button(b, text='测延迟(选中)', command=self.test_selected)
        self.btn_test_sel.pack(side='left')
        self.btn_test_all = ttk.Button(b, text='测延迟(全部)', command=self.test_all)
        self.btn_test_all.pack(side='left', padx=6)
        ttk.Button(b, text='🛡 IP 体检', command=self.ip_check_click).pack(side='left', padx=6)
        ttk.Separator(b, orient='vertical').pack(side='left', fill='y', padx=8)
        self.btn_conn = ttk.Button(b, text='▶ 连接', style='Primary.TButton',
                                   command=self.connect_selected)
        self.btn_conn.pack(side='left')
        ttk.Button(b, text='■ 断开', command=self.disconnect).pack(side='left', padx=6)
        ttk.Button(b, text='删除', style='Danger.TButton',
                   command=self.delete_selected).pack(side='left')
        ttk.Button(b, text='⚙ 设置', style='Ghost.TButton',
                   command=self.open_settings).pack(side='right')
        ttk.Button(b, text='⬇ 下载器', style='Ghost.TButton',
                   command=self.open_downloader).pack(side='left', padx=(10, 0))

        # ---- 筛选行 ----
        info = tk.Frame(self.root, bg=BG)
        info.pack(fill='x', padx=14, pady=(10, 4))
        tk.Label(info, text='地区', bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9, 'bold')).pack(side='left')
        self.region_combo = ttk.Combobox(info, textvariable=self.region_var, width=12,
                                         state='readonly')
        self.region_combo.pack(side='left', padx=(6, 14))
        self.region_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        tk.Label(info, text='搜索', bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9, 'bold')).pack(side='left')
        ttk.Entry(info, textvariable=self.filter_text, width=30).pack(side='left', padx=6)
        tk.Label(info, textvariable=self.count_var, bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9)).pack(side='right')

        # ---- 状态栏（底部）----
        sb = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        sb.pack(fill='x', side='bottom')
        self.usage_var = tk.StringVar()
        self.meta_var = tk.StringVar()
        tk.Label(sb, textvariable=self.usage_var, bg=CARD, fg=MUTED,
                 font=('Microsoft YaHei UI', 9), anchor='w', padx=14, pady=6).pack(side='left')
        tk.Label(sb, textvariable=self.meta_var, bg=CARD, fg=MUTED,
                 font=('Microsoft YaHei UI', 9), anchor='e', padx=12, pady=6).pack(side='right')
        ttk.Button(sb, text='📊 连接统计', style='Ghost.TButton',
                   command=self.show_conn_stats).pack(side='right', padx=4, pady=2)

        # ---- 日志卡片（底部，仿 v2rayN 运行日志）----
        logcard = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        logcard.pack(fill='x', side='bottom', padx=10, pady=(4, 6))
        lc = tk.Frame(logcard, bg=CARD, padx=8, pady=6)
        lc.pack(fill='both', expand=True)
        logbar = tk.Frame(lc, bg=CARD)
        logbar.pack(fill='x', pady=(0, 3))
        tk.Label(logbar, text='●', bg=CARD, fg=ACCENT,
                 font=('Microsoft YaHei UI', 9)).pack(side='left', padx=(0, 4))
        tk.Label(logbar, text='运行日志', bg=CARD, fg=TEXT,
                 font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        for text, cmd in (('清空', self.clear_log), ('暂停滚动', self.toggle_log_pause),
                          ('放大窗口', self.open_log_window), ('导出', self.export_log),
                          ('日志目录', self.open_log_dir)):
            ttk.Button(logbar, text=text, style='Ghost.TButton', command=cmd).pack(side='left', padx=(4, 0))
        self.log_pause_btn = None
        tk.Label(logbar, text='级别 ' + str(self.cfg.get('core_log_level', 'info')),
                 bg=CARD, fg=MUTED, font=('Microsoft YaHei UI', 8)).pack(side='right')
        self.log_box = tk.Text(lc, height=6, wrap='none', font=('Consolas', 9),
                               bg='#0f172a', fg='#cbd5e0', relief='flat', bd=0,
                               insertbackground='white', padx=6, pady=4)
        self.log_box.pack(side='left', fill='both', expand=True)
        ls = ttk.Scrollbar(lc, orient='vertical', command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=ls.set)
        ls.pack(side='left', fill='y')
        self._init_log_tags(self.log_box)
        self.log_box.bind('<MouseWheel>', lambda e: self.log_box.yview_scroll(int(-e.delta / 120), 'units'))

        # ---- 节点表格 ----
        card = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill='both', expand=True, padx=10, pady=(6, 0))
        inner_card = tk.Frame(card, bg=CARD, padx=6, pady=6)
        inner_card.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(inner_card, columns=COLS, show='headings', selectmode='browse')
        for c in COLS:
            self.tree.heading(c, text=HEADS[c], command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=WIDTHS[c],
                             anchor='w' if c in ('remark', 'addr') else 'center')
        vs = ttk.Scrollbar(inner_card, orient='vertical', command=self.tree.yview)
        hs = ttk.Scrollbar(inner_card, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        inner_card.rowconfigure(0, weight=1)
        inner_card.columnconfigure(0, weight=1)
        self.tree.tag_configure('odd', background=CARD)
        self.tree.tag_configure('even', background='#f8fafc')
        self.tree.tag_configure('connected', background='#e6fffa', foreground=OKC)
        self.tree.tag_configure('dead', foreground='#c53030')
        self.tree.bind('<Double-1>', lambda e: self.connect_selected())
        self.tree.bind('<Button-3>', self._context_menu)

        # ---- 右键菜单 ----
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label='连接', command=self.connect_selected)
        self.menu.add_command(label='测延迟', command=self.test_selected)
        self.menu.add_separator()
        self.menu.add_command(label='复制节点链接', command=self.copy_link)
        self.menu.add_command(label='删除节点', command=self.delete_selected)

    # ---------------- 状态 / 时区 ----------------
    def _set_pill(self, text, kind=None):
        if kind is None:
            if '已连接' in text:
                kind = 'ok'
            elif '失败' in text or '异常' in text:
                kind = 'err'
            elif '未连接' in text:
                kind = 'idle'
            else:
                kind = 'busy'
        colors = {'idle': '#a0aec0', 'ok': '#38a169', 'err': '#e53e3e', 'busy': '#3182ce'}
        try:
            self.status_pill.configure(text=text, bg=colors.get(kind, '#3182ce'))
        except Exception:
            pass

    def _auto_ip_check(self):
        d = core.ip_health(self.cfg['http_port'])
        if not d:
            self.log('IP 体检：查询失败（可手动点「IP 体检」重试）')
            return
        level, issues, _ = core.ip_health_verdict(d)
        tag = {'good': '✅ 干净', 'warn': '⚠ 机房 IP', 'bad': '⛔ 已被标记代理'}[level]
        extra = ''
        if d.get('timezone'):
            extra = f"  时区 {d['timezone']}"
        self.log(f"IP 体检：{tag} | {d.get('query')} {d.get('countryCode')} "
                 f"{(d.get('org') or d.get('isp') or '')[:32]}{extra}")

    def ip_check_click(self):
        if not self.core.running():
            messagebox.showinfo('IP 体检', '先连接一个节点再做体检')
            return

        def run():
            d = core.ip_health(self.cfg['http_port'])
            if not d:
                self.log('IP 体检：查询失败')
                self.q.put(('ip_dialog', None))
                return
            self.log(f"IP 体检：{d.get('query')} {d.get('country')} "
                     f"{d.get('org') or d.get('isp') or ''} "
                     f"{'（代理标记）' if d.get('proxy') else ''}"
                     f"{'（机房）' if d.get('hosting') else ''}")
            self.q.put(('ip_dialog', d))

        threading.Thread(target=run, daemon=True).start()

    def _show_ip_dialog(self, d):
        if not d:
            messagebox.showwarning('IP 体检', '查询失败，请确认已连接节点')
            return
        level, issues, tips = core.ip_health_verdict(d)
        icons = {'good': '✅', 'warn': '⚠️', 'bad': '⛔'}
        lines = [f"{icons[level]} 出口 IP：{d.get('query')}",
                 f"    地区：{d.get('country')} {d.get('regionName') or ''} {d.get('city') or ''}",
                 f"    运营商：{d.get('isp') or '?'}",
                 f"    ASN：{d.get('as') or '?'}",
                 f"    时区：{d.get('timezone') or '?'}",
                 f"    代理标记：{'是（高风险）' if d.get('proxy') else '否'}",
                 f"    机房 IP：{'是' if d.get('hosting') else '否'}",
                 '',
                 '【网站可能看到的"可疑点"】']
        if issues:
            lines += [f"  · {x}" for x in issues]
        else:
            lines.append('  · 暂无明显的机房/代理标记')
        lines += ['', '【让它看起来更安全的建议】']
        lines += [f"  · {x}" for x in (tips or ['保持节点固定，不要频繁换 IP'])]
        lines += [
            '  · 关闭浏览器 WebRTC（Edge: 设置→隐私→WebRTC 关闭，或装 WebRTC 控制扩展）',
            '  · 系统时区/语言与节点地区一致（本工具的「时区跟随」已处理时区）',
            '  · 关闭 IPv6：网络适配器属性里取消 IPv6，避免代理只走 IPv4 时 IPv6 直连泄漏',
            '  · 不要用同一 IP 登录多个账号；固定一个节点长期使用',
            '  · 最有效的还是换「住宅/家宽/原生 IP」节点（机场里通常标"原生"或"住宅"）',
        ]
        win = tk.Toplevel(self.root)
        win.title('IP 体检')
        win.transient(self.root)
        win.geometry('640x520')
        win.configure(bg=BG)
        txt = tk.Text(win, wrap='word', font=('Microsoft YaHei UI', 10), bg='#ffffff',
                      fg='#2d3748', relief='solid', bd=1, padx=10, pady=10)
        txt.pack(fill='both', expand=True, padx=10, pady=10)
        txt.insert('1.0', '\n'.join(lines))
        txt.configure(state='disabled')
        ttk.Button(win, text='关闭', command=win.destroy).pack(pady=(0, 10))

    def _apply_tz(self):
        if not self.cfg.get('tz_follow'):
            return
        info = core.geo_timezone(self.cfg['http_port'])
        if not info or not info.get('win'):
            self.log('⚠ 未能确定节点时区，跳过时区切换')
            return
        if not core.is_admin():
            self.log('⚠ 改时区需要管理员权限：请用「启动迷你v2rayN(管理员).cmd」启动（设置里也有按钮）')
            return
        ok, msg = core.set_timezone(info['win'])
        label = core.tz_label(info['win'])
        if ok:
            self.log(f"✓ 系统时区已切换为 {label}（节点 {info.get('ip')} {info.get('country')}）")
        else:
            self.log(f'✗ 时区切换失败: {msg}')

    def _restore_tz(self):
        if not self.cfg.get('tz_follow'):
            return
        if not core.is_admin():
            return
        target = self.cfg.get('tz_restore') or 'China Standard Time'
        if core.current_timezone() == target:
            return
        ok, msg = core.set_timezone(target)
        if ok:
            self.log('✓ 系统时区已恢复为北京时间 (UTC+8)')
        else:
            self.log(f'✗ 恢复时区失败: {msg}')

    def create_shortcut(self, admin=False):
        """在桌面创建带图标的快捷方式（admin=True 时自动以管理员身份运行）。"""
        try:
            pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
            if not os.path.exists(pyw):
                pyw = sys.executable
            script = os.path.join(HERE, 'steadylink.py')
            icon = ICON_ICO if os.path.exists(ICON_ICO) else ''
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            name = 'SteadyLink (Admin).lnk' if admin else 'SteadyLink.lnk'
            lnk = os.path.join(desktop, name)
            ps = (
                '$ws = New-Object -ComObject WScript.Shell; '
                f'$sc = $ws.CreateShortcut("{lnk}"); '
                f'$sc.TargetPath = "{pyw}"; '
                f"$sc.Arguments = '\"{script}\"'; "
                f'$sc.WorkingDirectory = "{HERE}"; '
                + (f'$sc.IconLocation = "{icon}"; ' if icon else '')
                + f'$sc.Description = "SteadyLink"; $sc.Save()'
            )
            r = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                               capture_output=True, text=True, timeout=30,
                               creationflags=core.CREATE)
            if r.returncode != 0 or not os.path.exists(lnk):
                self.log(f'✗ 创建快捷方式失败: {(r.stderr or r.stdout)[:200]}')
                return
            if admin:
                # 给 .lnk 打上 RunAsAdministrator 标志（0x15 字节 0x20 位）
                b = bytearray(open(lnk, 'rb').read())
                if len(b) > 0x15:
                    b[0x15] = b[0x15] | 0x20
                    open(lnk, 'wb').write(bytes(b))
            self.log(f'✓ 已创建桌面快捷方式 {name}' + ('（管理员）' if admin else ''))
            messagebox.showinfo('快捷方式', f'已在桌面创建 {name}')
        except Exception as e:
            self.log(f'✗ 创建快捷方式异常: {e}')

    def relaunch_as_admin(self):
        if core.is_admin():
            messagebox.showinfo('权限', '当前已经是管理员权限')
            return
        try:
            script = os.path.join(HERE, 'steadylink.py')
            rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable,
                                                     f'"{script}"', HERE, 1)
            if rc > 32:
                self._real_quit()
            else:
                messagebox.showerror('权限', f'提权失败（错误码 {rc}）')
        except Exception as e:
            messagebox.showerror('权限', f'提权失败：{e}')

    def _context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.menu.tk_popup(event.x_root, event.y_root)

    # ---------------- 队列 / 定时 ----------------
    def log(self, msg):
        self.q.put(('log', msg))

    def _init_log_tags(self, widget):
        widget.tag_configure('info', foreground='#cbd5e0')
        widget.tag_configure('warn', foreground='#f6ad55')
        widget.tag_configure('err', foreground='#fc8181')
        widget.tag_configure('debug', foreground='#718096')
        widget.tag_configure('core', foreground='#90cdf4')
        widget.tag_configure('direct', foreground='#68d391')
        widget.tag_configure('proxy', foreground='#63b3ed')

    @staticmethod
    def _classify(text):
        t = text or ''
        if 'ERROR' in t or '[Error]' in t or '✗' in t or '失败' in t:
            return 'err'
        if 'WARN' in t or '[Warning]' in t or '⚠' in t:
            return 'warn'
        if '[Debug]' in t or 'DEBUG' in t:
            return 'debug'
        if 'accepted //' in t:
            return 'direct' if ('-> direct' in t or '=> direct' in t) else 'proxy'
        if '[Info]' in t or '[' in t:
            return 'core'
        return 'info'

    def append_log(self, text, tag=None):
        if tag is None:
            tag = self._classify(text)
        self.log_lines.append(text)
        for widget in (self.log_box, getattr(self, 'log_win_text', None)):
            if widget is None:
                continue
            try:
                widget.insert('end', text + '\n', tag)
                if self.paused:
                    continue
                widget.see('end')
                # 限制行数
                total = int(widget.index('end-1c').split('.')[0])
                if total > 3000:
                    widget.delete('1.0', '800.0')
            except Exception:
                pass

    def clear_log(self):
        self.log_lines.clear()
        for widget in (self.log_box, getattr(self, 'log_win_text', None)):
            if widget is not None:
                try:
                    widget.delete('1.0', 'end')
                except Exception:
                    pass

    def toggle_log_pause(self):
        self.paused = not getattr(self, 'paused', False)
        self.log(f'日志滚动已{"暂停" if self.paused else "恢复"}')

    def open_log_dir(self):
        try:
            os.startfile(core.CFG_DIR)
        except Exception as e:
            self.log(f'打开日志目录失败: {e}')

    def export_log(self):
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(defaultextension='.log',
                                                initialfile='steadylink.log',
                                                filetypes=[('日志文件', '*.log'), ('文本', '*.txt')])
            if not path:
                return
            open(path, 'w', encoding='utf-8').write('\n'.join(self.log_lines))
            self.log(f'日志已导出: {path}')
        except Exception as e:
            self.log(f'导出失败: {e}')

    def open_log_window(self):
        if getattr(self, 'log_win', None) and self.log_win.winfo_exists():
            self.log_win.lift()
            return
        win = tk.Toplevel(self.root)
        self.log_win = win
        win.title('SteadyLink — 运行日志')
        win.geometry('1000x640')
        win.configure(bg=BG)
        bar = tk.Frame(win, bg=CARD, padx=10, pady=8)
        bar.pack(fill='x')
        tk.Label(bar, text='运行日志（实时）', bg=CARD, fg='#2d3748',
                 font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        for text, cmd in (('清空', self.clear_log), ('暂停滚动', self.toggle_log_pause),
                          ('导出', self.export_log)):
            ttk.Button(bar, text=text, command=cmd).pack(side='left', padx=(8, 0))
        wrap = tk.Frame(win, bg='#0f172a')
        wrap.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.log_win_text = tk.Text(wrap, wrap='none', font=('Consolas', 10),
                                    bg='#0f172a', fg='#cbd5e0', relief='flat', bd=0,
                                    insertbackground='white', padx=8, pady=6)
        self.log_win_text.pack(side='left', fill='both', expand=True)
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.log_win_text.yview)
        self.log_win_text.configure(yscrollcommand=vs.set)
        vs.pack(side='left', fill='y')
        self._init_log_tags(self.log_win_text)
        if self.log_lines:
            self.log_win_text.insert('end', '\n'.join(self.log_lines) + '\n', 'info')
        self.log_win_text.see('end')
        win.protocol('WM_DELETE_WINDOW', self._close_log_window)

    def _close_log_window(self):
        try:
            self.log_win_text = None
            if self.log_win:
                self.log_win.destroy()
        except Exception:
            pass
        self.log_win = None

    def _restart_access_tail(self):
        self._tail_stop.set()
        self._tail_stop = threading.Event()
        stop = self._tail_stop
        path = os.path.join(core.CFG_DIR, 'access.log')

        def run():
            pos = 0
            while not stop.is_set():
                try:
                    if not os.path.exists(path):
                        time.sleep(0.3)
                        continue
                    with open(path, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(pos)
                        while not stop.is_set():
                            line = f.readline()
                            if line:
                                pos = f.tell()
                                self.q.put(('logline', line.rstrip()))
                            else:
                                time.sleep(0.2)
                except Exception:
                    time.sleep(0.5)

        threading.Thread(target=run, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'log':
                    self.append_log(f"{time.strftime('%H:%M:%S')} {payload}", None)
                elif kind == 'logline':
                    self.append_log(payload, None)
                elif kind == 'refresh':
                    self.refresh()
                elif kind == 'status':
                    self.status.set(payload)
                    self._set_pill(payload)
                elif kind == 'usage':
                    self.update_usage_bar()
                elif kind == 'test_done':
                    self.testing = False
                    self._set_test_buttons(True)
                    if self.cfg.get('auto_sort_after_test'):
                        self.sort_col, self.sort_desc = 'lat', False
                        self.refresh()
                elif kind == 'ask_connect':
                    self._do_connect(payload)
                elif kind == 'ip_dialog':
                    self._show_ip_dialog(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _tick(self):
        try:
            if self.core.running():
                self.usage.poll(self.cfg)
                self.usage.snapshot()
        except Exception:
            pass
        self.update_usage_bar()
        self._maybe_auto_update()
        self.root.after(15000, self._tick)

    def _maybe_auto_update(self):
        hours = float(self.cfg.get('auto_update_hours') or 0)
        if hours <= 0 or self.updating or not self.cfg.get('subscription'):
            return
        if time.time() - float(self.cfg.get('last_sub_update') or 0) >= hours * 3600:
            self.log(f'到达自动更新间隔（{hours} 小时），正在更新订阅 ...')
            self.update_sub(silent=True)

    def update_usage_bar(self):
        up, down = self.usage.live()
        d = self.usage.committed
        self.usage_var.set(
            f"今日 ↑{core.fmt_bytes(d.get('today_up', 0) + up)}  ↓{core.fmt_bytes(d.get('today_down', 0) + down)}"
            f"    累计 ↑{core.fmt_bytes(d.get('total_up', 0) + up)}  ↓{core.fmt_bytes(d.get('total_down', 0) + down)}"
            f"    会话数 {d.get('sessions', 0)}"
        )
        self.meta_var.set(f"端口 HTTP {self.cfg['http_port']} · {core.tz_label(core.current_timezone()).split(' (')[0]}")

    # ---------------- 列表 / 排序 / 过滤 ----------------
    def region_of(self, n):
        rem = n.get('remark', '')
        for _, zh in REGION_MAP:
            if zh in rem:
                return zh
        loc = (n.get('loc') or '').strip()
        code_map = {'US': '美国', 'JP': '日本', 'KR': '韩国', 'SG': '新加坡', 'GB': '英国',
                    'DE': '德国', 'HK': '香港', 'TW': '台湾', 'MY': '马来西亚',
                    'NL': '荷兰', 'CH': '瑞士', 'FR': '法国'}
        if loc in code_map:
            return code_map[loc]
        return '其他'

    def node_state(self, n):
        if n.get('link') == self.connected_link:
            return 'connected'
        if n.get('lat'):
            return 'ok'
        if n.get('tested_at'):
            return 'dead'
        return 'untested'

    def _state_text(self, n):
        st = self.node_state(n)
        return {'connected': ('● 已连接', 'connected'), 'ok': ('✓ 可用', ''),
                'dead': ('✗ 不通', 'dead'), 'untested': ('未测', '')}[st]

    def _sort_key(self, i):
        n = self.nodes[i]
        col = self.sort_col
        if col == 'lat':
            lat = n.get('lat')
            return (lat is None, lat if lat else 0)
        if col == 'port':
            return (n.get('port') or 0)
        if col == 'state':
            order = {'connected': 0, 'ok': 1, 'dead': 2, 'untested': 3}
            return order[self.node_state(n)]
        return str(n.get(col) or '').lower()

    def sort_by(self, col):
        if col not in SORTABLE:
            return
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, False
        self.refresh()

    def _visible_indexes(self):
        idx = list(range(len(self.nodes)))
        rg = self.region_var.get()
        if rg and rg != '全部':
            idx = [i for i in idx if self.region_of(self.nodes[i]) == rg]
        ft = self.filter_text.get().strip().lower()
        if ft:
            def hit(i):
                n = self.nodes[i]
                hay = ' '.join(str(n.get(k, '')) for k in ('remark', 'addr', 'loc', 'ip', 'proto', 'port')).lower()
                return ft in hay
            idx = [i for i in idx if hit(i)]
        idx.sort(key=lambda i: (self._sort_key(i), i), reverse=self.sort_desc)
        return idx

    def refresh(self):
        sel = self.tree.selection()
        sel_iid = sel[0] if sel else None
        regions = {}
        for n in self.nodes:
            r = self.region_of(n)
            regions[r] = regions.get(r, 0) + 1
        vals = ['全部'] + [r for r, _ in sorted(regions.items(), key=lambda x: -x[1])]
        self.region_combo.configure(values=vals)
        if self.region_var.get() not in vals:
            self.region_var.set('全部')
        self.tree.delete(*self.tree.get_children())
        idx = self._visible_indexes()
        for i in idx:
            n = self.nodes[i]
            state, tag = self._state_text(n)
            if tag == 'connected':
                tags = ('connected',)
            elif tag == 'dead':
                tags = ('dead',)
            else:
                tags = ('even' if i % 2 else 'odd',)
            lat = f"{n['lat']}ms" if n.get('lat') else '—'
            self.tree.insert('', 'end', iid=str(i), tags=tags, values=(
                n.get('remark', '')[:42], n.get('addr', ''), n.get('port', ''),
                n.get('proto', ''), lat, n.get('ip', '') or '—',
                n.get('loc', '') or self.region_of(n), state))
        if sel_iid and self.tree.exists(sel_iid):
            self.tree.selection_set(sel_iid)
        for c in COLS:
            mark = ''
            if c == self.sort_col:
                mark = ' ▼' if self.sort_desc else ' ▲'
            self.tree.heading(c, text=HEADS[c] + mark)
        total = len(self.nodes)
        alive = sum(1 for n in self.nodes if n.get('lat'))
        shown = len(idx)
        self.count_var.set(f'显示 {shown} / 共 {total} 个 · 可用 {alive} 个')

    def selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except ValueError:
            return None

    # ---------------- 导入 / 更新订阅 ----------------
    def _maybe_first_import(self):
        if self.nodes:
            return
        imported = 0
        for f in ('节点.txt', '备用节点.txt'):
            p = os.path.join(HERE, '..', '付费节点', f)
            if not os.path.exists(p):
                continue
            try:
                for line in open(p, encoding='utf-8'):
                    line = line.strip()
                    if line.startswith(('vless://', 'vmess://', 'trojan://', 'ss://')):
                        if self._add_link(line, 'manual'):
                            imported += 1
            except Exception:
                pass
        if imported:
            self.save()
            self.log(f"首次启动：从「付费节点」文件夹导入 {imported} 个节点")

    def _add_link(self, link, source='manual', keep=None):
        n = core.parse_node(link)
        if not core.valid_node(n):
            return False
        if any(w in (n.get('remark') or '') for w in INFO_WORDS):
            return False  # 跳过"剩余流量/套餐到期"这类信息节点
        for x in self.nodes:
            if x.get('link') == link:
                return False
        rec = {'link': link, 'remark': pretty_remark(n['remark']), 'addr': n['addr'],
               'port': n['port'], 'proto': n['proto'], 'lat': None, 'ip': '', 'loc': '',
               'tested_at': 0, 'source': source}
        if keep:
            rec.update({k: keep.get(k) for k in ('lat', 'ip', 'loc', 'tested_at')})
        self.nodes.append(rec)
        return True

    def import_sub(self):
        url = simpledialog.askstring('导入订阅', '订阅链接（v2ray 格式）：',
                                     initialvalue=self.cfg.get('subscription', ''), parent=self.root)
        if not url:
            return
        self.cfg['subscription'] = url.strip()
        core.save_config(self.cfg)
        self._run_subscription_update(interactive=True)

    def update_sub(self, silent=False):
        if not self.cfg.get('subscription'):
            if silent:
                return
            if not messagebox.askyesno('更新订阅', '还没设置订阅链接，现在去设置吗？'):
                return
            return self.import_sub()
        if self.updating:
            return
        self._run_subscription_update(interactive=not silent)

    def _run_subscription_update(self, interactive=True):
        if self.updating:
            return
        self.updating = True
        self.btn_update.configure(state='disabled')
        url = self.cfg['subscription']

        def run():
            try:
                proxy = self.cfg['http_port'] if self.core.running() else None
                raw, err = core.fetch_subscription(url, proxy)
                if err or not raw:
                    self.log(f'订阅更新失败：{err}')
                    if interactive:
                        self.root.after(0, lambda: messagebox.showerror('订阅', f'更新失败：{err}'))
                    return
                links = core.decode_links(raw)
                parsed = [(l, core.parse_node(l)) for l in links]
                parsed = [(l, p) for l, p in parsed
                          if p and core.valid_node(p) and not any(w in (p.get('remark') or '') for w in INFO_WORDS)]
                old = {n['link']: n for n in self.nodes}
                kept, added = [], 0
                new_links = set()
                for l, p in parsed:
                    new_links.add(l)
                    if l in old:
                        kept.append(old[l])
                    else:
                        kept.append({'link': l, 'remark': pretty_remark(p['remark']),
                                     'addr': p['addr'], 'port': p['port'], 'proto': p['proto'],
                                     'lat': None, 'ip': '', 'loc': '', 'tested_at': 0,
                                     'source': 'sub'})
                        added += 1
                removed = sum(1 for n in self.nodes
                              if n.get('source') == 'sub' and n.get('link') not in new_links)
                # 保留手工节点 + 当前连接中的节点
                for n in self.nodes:
                    if n.get('source') != 'sub' or n.get('link') == self.connected_link:
                        if n.get('link') not in new_links and n not in kept:
                            kept.append(n)
                self.nodes = kept
                self.save()
                self.cfg['last_sub_update'] = time.time()
                core.save_config(self.cfg)
                self.log(f'订阅更新完成：共 {len(parsed)} 条，新增 {added} 条，移除 {removed} 条')
                self.q.put(('refresh', None))
            except Exception as e:
                self.log(f'订阅更新异常: {e}')
            finally:
                self.updating = False
                self.q.put(('sub_done', None))

        threading.Thread(target=run, daemon=True).start()

    def import_clipboard(self):
        try:
            txt = self.root.clipboard_get()
        except Exception:
            messagebox.showinfo('剪贴板', '剪贴板里没有文本')
            return
        added = 0
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith(('vless://', 'vmess://', 'trojan://', 'ss://')):
                if self._add_link(line, 'manual'):
                    added += 1
        if added:
            self.save()
            self.refresh()
            self.log(f'从剪贴板导入 {added} 条')
        else:
            messagebox.showinfo('剪贴板', '没识别到新节点（需 vless:// vmess:// trojan:// ss:// 链接）')

    def save(self):
        core.save_nodes(self.nodes)

    # ---------------- 测速 ----------------
    def _set_test_buttons(self, enabled):
        state = 'normal' if enabled else 'disabled'
        self.btn_test_sel.configure(state=state)
        self.btn_test_all.configure(state=state)

    def test_selected(self):
        idx = self.selected_index()
        if idx is None:
            messagebox.showinfo('测延迟', '先选中一行节点')
            return
        self._start_test([idx])

    def test_all(self):
        if not self.nodes:
            return
        self._start_test(list(range(len(self.nodes))))

    def _start_test(self, indexes):
        if self.testing:
            return
        self.testing = True
        self._set_test_buttons(False)
        self.log(f'开始测速 {len(indexes)} 个节点 ...')

        def run():
            try:
                pairs = []
                for i in indexes:
                    p = core.parse_node(self.nodes[i]['link'])
                    if p:
                        pairs.append((i, p))
                results = core.test_nodes([p for _, p in pairs])
                ok = 0
                for k, (i, p) in enumerate(pairs):
                    r = results.get(k, {})
                    n = self.nodes[i]
                    n['lat'] = r.get('lat')
                    n['tested_at'] = time.time()
                    if r.get('ok'):
                        ok += 1
                self.save()
                self.log(f'测速完成：{ok}/{len(pairs)} 可用（可点表头按延迟/状态排序）')
            except Exception as e:
                self.log(f'测速出错: {e}')
            finally:
                self.q.put(('refresh', None))
                self.q.put(('test_done', None))

        threading.Thread(target=run, daemon=True).start()

    # ---------------- 连接 ----------------
    def connect_selected(self):
        idx = self.selected_index()
        if idx is None:
            messagebox.showinfo('连接', '先选中一行节点')
            return
        node = core.parse_node(self.nodes[idx]['link'])
        if not node:
            messagebox.showerror('连接', '节点配置解析失败')
            return
        self.q.put(('ask_connect', (idx, node)))

    def _do_connect(self, payload):
        idx, node = payload
        if self.core.running():
            self.disconnect(silent=True)

        self.conn_gen += 1
        gen = self.conn_gen

        def run():
            self.log(f"正在连接 {node['remark'][:40]} ({node['addr']}:{node['port']}) ...")
            token = self.core.start(node, self.cfg,
                                    on_line=lambda line: self.q.put(('logline', line)))
            if not token:
                self.log('核心启动失败（节点配置或端口问题）')
                self.q.put(('status', '● 连接失败'))
                return
            if self.conn_gen != gen:
                self.core.stop(token)
                return
            ok, ip, loc, lat = core.health(self.cfg['http_port'])
            if self.conn_gen != gen:
                self.core.stop(token)
                return
            if not ok:
                self.log('健康检查失败：节点不通')
                self.core.stop(token)
                self.q.put(('status', '● 连接失败'))
                return
            proxy_ok = core.set_system_proxy(self.cfg['http_port'])
            if not proxy_ok:
                self.log('⚠ 系统代理设置失败（备份无效或注册表写入被拒绝），当前只有浏览器之外的程序可手动用 10901')
            self.connected_link = self.nodes[idx]['link']
            self.exit_ip = ip
            self.active_node = node
            self.conn_sess = {'node': node.get('remark'), 'link': node.get('link'),
                              'start': time.time(), 'ip_start': ip, 'reconnects': 0,
                              'recover_times': [], 'ip_changes': 0, 'online': 0.0,
                              'check_ok': False, 'check_time': None,
                              'failures': 0, 'failovers': 0}
            self.nodes[idx]['ip'] = ip
            self.nodes[idx]['loc'] = loc
            self.nodes[idx]['lat'] = lat
            self.nodes[idx]['tested_at'] = time.time()
            self.save()
            self.log(f"✓ 已连接  出口 {loc} {ip}  延迟 {lat}ms")
            self.log('出口 IP 已固定，之后不要频繁换节点（防 Claude 风控）')
            self.q.put(('status', f'● 已连接  {loc} {ip}  延迟 {lat}ms'
                        + ('' if proxy_ok else '（系统代理未生效）')))
            self.q.put(('refresh', None))
            self._restart_access_tail()
            self._apply_tz()
            if self.cfg.get('auto_ip_check', True):
                self._auto_ip_check()
            self._start_monitor(node)

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _account_check(sess, ok, now):
        if sess is None:
            return
        if ok:
            if sess.get('check_ok') and sess.get('check_time'):
                sess['online'] = sess.get('online', 0.0) + min(now - sess['check_time'], 60)
            sess['check_ok'] = True
            sess['check_time'] = now
        else:
            sess['failures'] = sess.get('failures', 0) + 1
            sess['check_ok'] = False
            sess['check_time'] = now

    def _start_monitor(self, node):
        self.active_node = self.active_node or node
        self.monitor_stop.set()
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=2)
        self.monitor_stop = threading.Event()
        stop = self.monitor_stop

        def loop():
            fails = 0
            while not stop.is_set():
                if stop.wait(30):
                    return
                ok, ip, loc, lat = core.health(self.cfg['http_port'])
                if stop.is_set():
                    return
                sess = self.conn_sess
                now = time.time()
                if ok:
                    self._account_check(sess, True, now)
                    if ip and self.exit_ip and ip != self.exit_ip:
                        self.log(f'⚠ 出口 IP 变了: {self.exit_ip} -> {ip}（Claude 风控风险）')
                        self.exit_ip = ip
                        if sess is not None:
                            sess['ip_changes'] = sess.get('ip_changes', 0) + 1
                    if fails:
                        self.log(f'✓ 节点恢复  延迟 {lat}ms')
                        if sess is not None:
                            sess.setdefault('recover_times', []).append(30.0 * fails)
                    fails = 0
                    self.q.put(('status', f'● 已连接  {loc} {ip}  延迟 {lat}ms'))
                else:
                    fails += 1
                    self._account_check(sess, False, now)
                    self.log(f'✗ 健康检查失败（{fails}/2）')
                    if fails < 2:
                        continue
                    fails = 0
                    if not self.cfg.get('auto_reconnect', True):
                        self.log('节点异常：自动重连已关闭，请手动处理')
                        self.q.put(('status', '● 节点异常（未自动重连）'))
                        continue
                    if stop.is_set():
                        return
                    self._attempt_recover(stop)

        self.monitor_thread = threading.Thread(target=loop, daemon=True)
        self.monitor_thread.start()

    def _attempt_recover(self, stop):
        """用当前活动节点重连（保持出口 IP）。用户断开/换节点后代际变化会中止。"""
        node = self.active_node
        if node is None:
            return False
        gen = self.conn_gen
        self.q.put(('status', '● 节点异常，正在重连...'))
        for attempt in (1, 2):
            if stop.is_set() or self.conn_gen != gen:
                return False
            t0 = time.time()
            self.log(f'尝试重连原节点（第 {attempt} 次）...')
            self.core.stop(self.core.current_token())
            if stop.is_set() or self.conn_gen != gen:
                return False
            token = self.core.start(node, self.cfg,
                                    on_line=lambda line: self.q.put(('logline', line)))
            if token:
                if stop.is_set() or self.conn_gen != gen:
                    self.core.stop(token)
                    return False
                ok, ip, loc, lat = core.health(self.cfg['http_port'])
                if stop.is_set() or self.conn_gen != gen:
                    self.core.stop(token)
                    return False
                if ok:
                    dt = time.time() - t0
                    self.log(f'✓ 重连成功，用时 {dt:.1f}s  出口 {loc} {ip}')
                    sess = self.conn_sess
                    if sess is not None:
                        sess['reconnects'] = sess.get('reconnects', 0) + 1
                        sess.setdefault('recover_times', []).append(dt)
                        sess['check_ok'] = True
                        sess['check_time'] = time.time()
                    self.exit_ip = ip
                    self._restart_access_tail()
                    self._apply_tz()
                    self.q.put(('status', f'● 已连接  {loc} {ip}  延迟 {lat}ms'))
                    return True
            time.sleep(3)
        self.log('✗ 原节点重连失败')
        if self.cfg.get('auto_failover') and not stop.is_set() and self.conn_gen == gen:
            return self._failover(stop)
        self.q.put(('status', '● 节点异常（未自动切换）'))
        return False

    def _failover(self, stop):
        """仅当用户开启 auto_failover 时切换节点（会改变出口 IP）。"""
        node = self.active_node
        gen = self.conn_gen
        cands = []
        for n in self.nodes:
            if node is not None and n.get('link') == node.get('link'):
                continue
            p = core.parse_node(n.get('link', ''))
            if p and core.valid_node(p):
                cands.append((n.get('lat') or 99999, n, p))
        cands.sort(key=lambda x: x[0])
        for _, n, p in cands[:3]:
            if stop.is_set() or self.conn_gen != gen:
                return False
            self.log(f'自动切换节点 → {n["remark"][:30]} ...')
            token = self.core.start(p, self.cfg,
                                    on_line=lambda line: self.q.put(('logline', line)))
            if token:
                if stop.is_set() or self.conn_gen != gen:
                    self.core.stop(token)
                    return False
                ok, ip, loc, lat = core.health(self.cfg['http_port'])
                if stop.is_set() or self.conn_gen != gen:
                    self.core.stop(token)
                    return False
                if ok:
                    self.active_node = p
                    self.connected_link = n['link']
                    self.exit_ip = ip
                    n['ip'], n['loc'], n['lat'] = ip, loc, lat
                    sess = self.conn_sess
                    if sess is not None:
                        sess['failovers'] = sess.get('failovers', 0) + 1
                        sess['ip_changes'] = sess.get('ip_changes', 0) + 1
                        sess['check_ok'] = True
                        sess['check_time'] = time.time()
                        sess['failover_to'] = n['remark']
                    self.log(f'✓ 已切换节点 {n["remark"][:30]}  出口 {loc} {ip}（IP 已变化）')
                    self._restart_access_tail()
                    self._apply_tz()
                    self.q.put(('status', f'● 已连接  {loc} {ip}  延迟 {lat}ms'))
                    self.q.put(('refresh', None))
                    return True
            if token:
                self.core.stop(token)
        self.log('✗ 备用节点均连接失败，请手动选择')
        self.q.put(('status', '● 节点异常（切换失败）'))
        return False

    def _close_conn_session(self):
        sess, self.conn_sess = self.conn_sess, None
        if not sess:
            return
        end = time.time()
        st = sess.get('start') or end
        online = float(sess.get('online') or 0.0)
        if sess.get('check_ok') and sess.get('check_time'):
            online += min(max(0.0, end - sess['check_time']), 60)
        rec = {'node': sess.get('node'), 'link': sess.get('link'),
               'start': st, 'end': end, 'duration': max(0.0, end - st),
               'online': round(online, 1),
               'failures': int(sess.get('failures', 0)),
               'reconnects': int(sess.get('reconnects', 0)),
               'failovers': int(sess.get('failovers', 0)),
               'ip_changes': int(sess.get('ip_changes', 0)),
               'recover_times': list(sess.get('recover_times') or []),
               'failover_to': sess.get('failover_to'),
               'start_str': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st)),
               'end_str': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end))}
        self.conn_hist.append(rec)
        if len(self.conn_hist) > 500:
            self.conn_hist = self.conn_hist[-500:]
        save_conn_hist(self.conn_hist)

    def _proxy_recovery_check(self):
        try:
            if core.recover_proxy_if_needed((self.cfg['http_port'],)):
                self.log('检测到上次异常退出遗留的系统代理，已恢复为原设置')
        except Exception:
            pass

    def show_conn_stats(self):
        hist = self.conn_hist
        win = tk.Toplevel(self.root)
        win.title('连接统计 — 节点稳定性')
        win.geometry('1000x600')
        win.configure(bg=BG)
        win.transient(self.root)
        top = tk.Frame(win, bg=CARD, padx=12, pady=8)
        top.pack(fill='x')
        tk.Label(top, text='节点稳定性统计', bg=CARD, fg='#1a202c',
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(side='left')
        tk.Label(top, text=f'  共 {len(hist)} 条连接记录', bg=CARD, fg='#718096',
                 font=('Microsoft YaHei UI', 9)).pack(side='left')

        cols = ('node', 'sessions', 'total', 'online', 'fails', 'recon', 'failover', 'ipchg', 'last')
        heads = {'node': '节点', 'sessions': '会话数', 'total': '会话总时长', 'online': '实际在线',
                 'fails': '故障次数', 'recon': '成功重连', 'failover': '故障切换',
                 'ipchg': 'IP 变化', 'last': '最近连接'}
        widths = {'node': 230, 'sessions': 65, 'total': 100, 'online': 100, 'fails': 75,
                  'recon': 80, 'failover': 80, 'ipchg': 75, 'last': 150}
        wrap = tk.Frame(win, bg=CARD, padx=8, pady=8)
        wrap.pack(fill='both', expand=True, padx=10, pady=(0, 6))
        tv = ttk.Treeview(wrap, columns=cols, show='headings')
        for c in cols:
            tv.heading(c, text=heads[c])
            tv.column(c, width=widths[c], anchor='w' if c == 'node' else 'center')
        vs = ttk.Scrollbar(wrap, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side='left', fill='both', expand=True)
        vs.pack(side='left', fill='y')

        def fmt_dur(sec):
            sec = int(sec or 0)
            if sec >= 3600:
                return f'{sec // 3600}h{sec % 3600 // 60:02d}m'
            if sec >= 60:
                return f'{sec // 60}m{sec % 60:02d}s'
            return f'{sec}s'

        agg = {}
        for r in hist:
            name = r.get('node') or '未知'
            a = agg.setdefault(name, {'sessions': 0, 'total': 0.0, 'online': 0.0,
                                      'fails': 0, 'recon': 0, 'failover': 0, 'ip': 0, 'last': ''})
            a['sessions'] += 1
            a['total'] += float(r.get('duration') or 0)
            a['online'] += float(r.get('online') or 0)
            a['fails'] += int(r.get('failures') or 0)
            a['recon'] += int(r.get('reconnects') or 0)
            a['failover'] += int(r.get('failovers') or 0)
            a['ip'] += int(r.get('ip_changes') or 0)
            if (r.get('end_str') or '') > a['last']:
                a['last'] = r.get('end_str') or ''

        for name in sorted(agg, key=lambda k: -agg[k]['online']):
            a = agg[name]
            tv.insert('', 'end', values=(name, a['sessions'], fmt_dur(a['total']),
                                         fmt_dur(a['online']), a['fails'], a['recon'],
                                         a['failover'], a['ip'], a['last']))

        bar = tk.Frame(win, bg=BG)
        bar.pack(fill='x', padx=10, pady=(0, 10))
        tk.Label(bar, text='记录保存在 连接历史.json；实际在线=健康检查为正常的累计时间，故障次数=连续失败触发重连的次数',
                 bg=BG, fg='#718096', font=('Microsoft YaHei UI', 8)).pack(side='left')

        def open_file():
            try:
                os.startfile(CONN_HIST_FILE)
            except Exception:
                pass

        def clear():
            if not messagebox.askyesno('连接统计', '清空所有连接记录？', parent=win):
                return
            self.conn_hist = []
            save_conn_hist([])
            win.destroy()

        ttk.Button(bar, text='打开记录文件', command=open_file).pack(side='right', padx=4)
        ttk.Button(bar, text='清空记录', command=clear).pack(side='right')

    def disconnect(self, silent=False):
        self.conn_gen += 1
        self.active_node = None
        self.monitor_stop.set()
        self._close_conn_session()
        try:
            self.usage.poll(self.cfg)
            self.usage.snapshot()
        except Exception:
            pass
        self.core.stop()
        try:
            self._tail_stop.set()
        except Exception:
            pass
        self._restore_tz()
        if not core.set_system_proxy(None):
            try:
                self.log('⚠ 系统代理还原失败（备份可能损坏），已尽可能关闭代理')
            except Exception:
                pass
        self.connected_link = None
        self.exit_ip = None
        self.q.put(('status', '● 未连接'))
        self.q.put(('refresh', None))
        self.q.put(('usage', None))
        if not silent:
            self.log('已断开，系统代理已还原（直连）')

    def delete_selected(self):
        idx = self.selected_index()
        if idx is None:
            return
        if self.connected_link == self.nodes[idx]['link']:
            messagebox.showinfo('删除', '这个节点正在使用，先断开再删')
            return
        if messagebox.askyesno('删除', f"删除节点：{self.nodes[idx]['remark'][:30]} ?"):
            self.nodes.pop(idx)
            self.save()
            self.refresh()

    def copy_link(self):
        idx = self.selected_index()
        if idx is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.nodes[idx]['link'])
        self.log('已复制节点链接到剪贴板')

    # ---------------- 设置 ----------------
    def open_downloader(self):
        DownloaderWindow(self)

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title('设置')
        win.transient(self.root)
        win.grab_set()
        win.geometry('620x880')
        win.configure(bg=BG)
        head = tk.Frame(win, bg=BG)
        head.pack(fill='x', padx=18, pady=(14, 0))
        tk.Label(head, text='设置', bg=BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w')
        frm = ttk.Frame(win, padding=(18, 10, 18, 16))
        frm.pack(fill='both', expand=True)
        vals = {}
        rows = (('http_port', 'HTTP 端口'), ('socks_port', 'SOCKS 端口'), ('stats_port', '统计 API 端口'))
        for i, (key, label) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky='w', pady=5)
            var = tk.StringVar(value=str(self.cfg[key]))
            ttk.Entry(frm, textvariable=var, width=12).grid(row=i, column=1, sticky='w', padx=10)
            vals[key] = var
        ttk.Label(frm, text='订阅链接').grid(row=3, column=0, sticky='w', pady=5)
        sub_var = tk.StringVar(value=self.cfg.get('subscription', ''))
        ttk.Entry(frm, textvariable=sub_var, width=46).grid(row=3, column=1, columnspan=2, sticky='we', padx=10)
        ttk.Label(frm, text='自动更新订阅(小时，0=关闭)').grid(row=4, column=0, sticky='w', pady=5)
        upd_var = tk.StringVar(value=str(self.cfg.get('auto_update_hours', 0)))
        ttk.Entry(frm, textvariable=upd_var, width=8).grid(row=4, column=1, sticky='w', padx=10)
        fa_var = tk.BooleanVar(value=bool(self.cfg.get('auto_failover')))
        ttk.Checkbutton(frm, text='自动故障切换（不建议用于 Claude：会换出口 IP）',
                        variable=fa_var).grid(row=5, column=0, columnspan=3, sticky='w', pady=8)
        sort_var = tk.BooleanVar(value=bool(self.cfg.get('auto_sort_after_test', True)))
        ttk.Checkbutton(frm, text='测速后自动按延迟排序', variable=sort_var).grid(
            row=6, column=0, columnspan=3, sticky='w', pady=2)
        tray_var = tk.BooleanVar(value=bool(self.cfg.get('minimize_to_tray', True)))
        cb_tray = ttk.Checkbutton(frm, text='最小化到托盘（关窗口不退出，托盘右键退出）',
                                  variable=tray_var)
        cb_tray.grid(row=7, column=0, columnspan=3, sticky='w', pady=2)
        if not TRAY_OK:
            cb_tray.configure(state='disabled')
        auto_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(frm, text='开机自启（登录后自动打开本工具）',
                        variable=auto_var).grid(row=8, column=0, columnspan=3, sticky='w', pady=2)

        rec_var = tk.BooleanVar(value=bool(self.cfg.get('auto_reconnect', True)))
        ttk.Checkbutton(frm, text='断线自动重连（优先原节点，保持出口 IP 不变）',
                        variable=rec_var).grid(row=20, column=0, columnspan=3, sticky='w', pady=2)
        ip_var = tk.BooleanVar(value=bool(self.cfg.get('auto_ip_check', True)))
        ttk.Checkbutton(frm, text='连接后自动做 IP 体检（记录 IP 风险画像）',
                        variable=ip_var).grid(row=13, column=0, columnspan=3, sticky='w', pady=2)
        ttk.Separator(frm, orient='horizontal').grid(row=9, column=0, columnspan=3,
                                                     sticky='we', pady=8)
        tz_var = tk.BooleanVar(value=bool(self.cfg.get('tz_follow', True)))
        cb_tz = ttk.Checkbutton(frm, text='连接后自动切换系统时区（断开恢复北京时间）',
                                variable=tz_var)
        cb_tz.grid(row=10, column=0, columnspan=3, sticky='w', pady=2)
        admin_txt = ('当前权限：管理员 ✓ 可切换时区' if core.is_admin()
                     else '当前权限：普通用户 ✗ 时区功能不可用，建议用管理员启动器')
        tk.Label(frm, text=admin_txt, bg=BG, fg=(OKC if core.is_admin() else WARNC),
                 font=('Microsoft YaHei UI', 9)).grid(row=11, column=0, columnspan=2,
                                                      sticky='w', pady=(2, 0))
        if not core.is_admin():
            ttk.Button(frm, text='以管理员身份重启',
                       command=lambda: (win.destroy(), self.relaunch_as_admin())).grid(
                row=11, column=2, sticky='e')

        ttk.Separator(frm, orient='horizontal').grid(row=14, column=0, columnspan=3,
                                                     sticky='we', pady=8)
        ttk.Label(frm, text='核心日志级别').grid(row=15, column=0, sticky='w', pady=4)
        level_var = tk.StringVar(value=str(self.cfg.get('core_log_level', 'info')))
        ttk.Combobox(frm, textvariable=level_var, width=10, state='readonly',
                     values=('none', 'error', 'warning', 'info', 'debug')).grid(
            row=15, column=1, sticky='w', padx=10)
        tk.Label(frm, text='（级别越高日志越详细，改完断开重连生效）', bg=BG, fg='#718096',
                 font=('Microsoft YaHei UI', 8)).grid(row=15, column=2, sticky='w')
        acc_var = tk.BooleanVar(value=bool(self.cfg.get('access_log', True)))
        ttk.Checkbutton(frm, text='记录访问日志（每个域名的路由明细，显示在运行日志里）',
                        variable=acc_var).grid(row=16, column=0, columnspan=3, sticky='w', pady=2)
        insecure_var = tk.BooleanVar(value=bool(self.cfg.get('allow_insecure', False)))
        ttk.Checkbutton(frm, text='允许不校验 TLS 证书（仅自签证书需要，会降低安全性）',
                        variable=insecure_var).grid(row=18, column=0, columnspan=3, sticky='w', pady=2)

        def save():
            try:
                for k, v in vals.items():
                    p = int(v.get())
                    if not (1024 < p < 65535):
                        raise ValueError
                    self.cfg[k] = p
                self.cfg['subscription'] = sub_var.get().strip()
                self.cfg['auto_update_hours'] = max(0.0, float(upd_var.get() or 0))
                self.cfg['auto_failover'] = bool(fa_var.get())
                self.cfg['auto_sort_after_test'] = bool(sort_var.get())
                tz_on = bool(tz_var.get())
                if (not tz_on) and self.cfg.get('tz_follow') and core.is_admin():
                    core.set_timezone(self.cfg.get('tz_restore') or 'China Standard Time')
                self.cfg['tz_follow'] = tz_on
                self.cfg['auto_ip_check'] = bool(ip_var.get())
                self.cfg['core_log_level'] = level_var.get()
                self.cfg['access_log'] = bool(acc_var.get())
                self.cfg['auto_reconnect'] = bool(rec_var.get())
                self.cfg['allow_insecure'] = bool(insecure_var.get())
                tray_wanted = bool(tray_var.get())
                self.cfg['minimize_to_tray'] = tray_wanted
                core.save_config(self.cfg)
                if tray_wanted:
                    self._setup_tray()
                elif self.tray is not None:
                    self._stop_tray()
                    self.log('托盘已关闭（关窗口将直接退出）')
                if set_autostart(auto_var.get()):
                    self.log('开机自启：' + ('已开启' if auto_var.get() else '已关闭'))
                else:
                    self.log('开机自启设置失败（写入启动文件夹出错）')
                win.destroy()
                self.refresh()
                self.log('设置已保存（端口变更需断开重连后生效）')
            except Exception:
                messagebox.showerror('设置', '端口需为 1024-65535 之间的整数', parent=win)

        shortcut_row = tk.Frame(frm, bg=BG)
        shortcut_row.grid(row=12, column=0, columnspan=3, sticky='w', pady=(8, 0))
        tk.Label(shortcut_row, text='桌面快捷方式：', bg=BG, fg='#4a5568',
                 font=('Microsoft YaHei UI', 9)).pack(side='left')
        ttk.Button(shortcut_row, text='创建普通模式',
                   command=lambda: self.create_shortcut(False)).pack(side='left', padx=4)
        ttk.Button(shortcut_row, text='创建管理员模式',
                   command=lambda: self.create_shortcut(True)).pack(side='left', padx=4)
        ttk.Button(frm, text='保存', style='Primary.TButton', command=save).grid(
            row=21, column=2, sticky='e', pady=12)

    # ---------------- 托盘 / 窗口 ----------------
    def _tray_image(self):
        if os.path.exists(TRAY_PNG):
            try:
                return Image.open(TRAY_PNG).convert('RGBA')
            except Exception:
                pass
        img = Image.new('RGB', (64, 64), (43, 108, 176))
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=40)
        except Exception:
            font = ImageFont.load_default()
        d.text((17, 8), 'V', fill='white', font=font)
        d.ellipse((4, 4, 60, 60), outline=(255, 255, 255), width=3)
        return img

    def _setup_tray(self):
        if not TRAY_OK:
            self.log('未安装 pystray/Pillow，托盘不可用；关窗口将直接退出')
            return
        if not self.cfg.get('minimize_to_tray', True):
            return
        if self.tray is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem('显示窗口', lambda: self.root.after(0, self.show_window), default=True),
            pystray.MenuItem('连接所选', lambda: self.root.after(0, self.connect_selected)),
            pystray.MenuItem('断开', lambda: self.root.after(0, lambda: self.disconnect())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出', lambda: self.root.after(0, self._real_quit)),
        )
        self.tray = pystray.Icon(APP_NAME, self._tray_image(), 'SteadyLink', menu)
        threading.Thread(target=self.tray.run, daemon=True).start()
        self.log('托盘图标已启用（关窗口会最小化到托盘）')

    def _stop_tray(self):
        t, self.tray = self.tray, None
        if t:
            try:
                t.stop()
            except Exception:
                pass

    def show_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def hide_to_tray(self):
        self.root.withdraw()
        self.log('已最小化到托盘（右键托盘图标可退出）')

    # ---------------- 收尾 ----------------
    def on_close(self):
        if self.tray is not None and not self._quitting:
            self.hide_to_tray()
            return
        self._real_quit()

    def _real_quit(self):
        self._quitting = True
        try:
            self.save()
            self.monitor_stop.set()
            self._restore_tz()
            self.disconnect(silent=True)
            core.kill_stale()
        except Exception:
            pass
        self._stop_tray()
        try:
            self.root.destroy()
        except Exception:
            pass



class DownloaderWindow(tk.Toplevel):
    """万能下载器：HTTP 多线程 / HLS(m3u8) / 磁力BT / 网页分析 / 站点代理规则 / 任务持久化 / 日志记录。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title('SteadyLink — 下载器')
        self.geometry('1080x820')
        self.configure(bg=BG)
        self.transient(app.root)
        self.rules = dl.load_rules()
        self.tasks = dl.load_tasks()
        self._logq = queue.Queue()
        self._snap = {'proxy': bool(app.cfg.get('dl_proxy', True)),
                      'threads': int(app.cfg.get('dl_threads', 8) or 8),
                      'aria': bool(dl.aria2_path())}
        for t in self.tasks:
            t.setdefault('kind', 'http')
            t.setdefault('stop', threading.Event())
            t.setdefault('speed', 0)
            t.setdefault('size', 0)
            t.setdefault('done', 0)
            t.setdefault('segs_total', 0)
            if t.get('status') in ('下载中', '探测中', '排队'):
                t['status'] = '已中断'
        self.history = dl.load_history()
        self.worker = None
        self._last_save = 0
        self.save_dir = app.cfg.get('dl_dir') or os.path.join(os.path.expanduser('~'), 'Downloads')
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except Exception:
            self.save_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
            os.makedirs(self.save_dir, exist_ok=True)

        head = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        head.pack(fill='x')
        h = tk.Frame(head, bg=CARD, padx=12, pady=8)
        h.pack(fill='x')
        tk.Label(h, text='万能下载器', bg=CARD, fg='#1a202c',
                 font=('Microsoft YaHei UI', 14, 'bold')).pack(side='left')
        tk.Label(h, text='  HTTP 多线程 / HLS / 磁力BT / 网页提取 / 代理规则', bg=CARD, fg='#718096',
                 font=('Microsoft YaHei UI', 9)).pack(side='left')
        self.aria_var = tk.StringVar(value='aria2: ' + ('就绪' if dl.aria2_path() else '未安装'))
        tk.Label(h, textvariable=self.aria_var, bg=CARD, fg='#718096',
                 font=('Microsoft YaHei UI', 8)).pack(side='right')

        row1 = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        row1.pack(fill='x', padx=10, pady=(8, 0))
        r1 = tk.Frame(row1, bg=CARD, padx=10, pady=8)
        r1.pack(fill='x')
        tk.Label(r1, text='链接', bg=CARD, fg='#4a5568', font=('Microsoft YaHei UI', 9, 'bold')).pack(side='left')
        self.url_var = tk.StringVar()
        e = ttk.Entry(r1, textvariable=self.url_var)
        e.pack(side='left', fill='x', expand=True, padx=8)
        e.bind('<Return>', lambda ev: self.start_direct())
        self.proxy_var = tk.BooleanVar(value=bool(app.cfg.get('dl_proxy', True)))
        ttk.Checkbutton(r1, text='走代理', variable=self.proxy_var).pack(side='left')
        default_backend = app.cfg.get('dl_backend') or ('aria2' if dl.aria2_path() else 'curl')
        self.backend_var = tk.BooleanVar(value=(default_backend == 'aria2'))
        ttk.Checkbutton(r1, text='多连接(aria2)', variable=self.backend_var,
                        style='Card.TCheckbutton').pack(side='left', padx=(8, 0))
        self.threads_var = tk.IntVar(value=int(app.cfg.get('dl_threads', 8)))
        tk.Label(r1, text='线程', bg=CARD, fg='#4a5568').pack(side='left', padx=(8, 2))
        ttk.Spinbox(r1, from_=1, to=16, width=4, textvariable=self.threads_var).pack(side='left')

        row2 = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        row2.pack(fill='x', padx=10, pady=(6, 0))
        r2 = tk.Frame(row2, bg=CARD, padx=10, pady=8)
        r2.pack(fill='x')
        ttk.Button(r2, text='⬇ 开始下载', style='Primary.TButton', command=self.start_direct).pack(side='left')
        ttk.Button(r2, text='🌐 分析网页', command=self.analyze_page).pack(side='left', padx=5)
        ttk.Button(r2, text='🔌 站点规则', command=self.edit_rules).pack(side='left')
        ttk.Button(r2, text='继续选中', command=self.resume_selected).pack(side='left', padx=5)
        ttk.Button(r2, text='取消选中', command=self.cancel_selected).pack(side='left')
        ttk.Button(r2, text='打开目录', command=self.open_dir_btn).pack(side='left', padx=5)
        self.dir_var = tk.StringVar(value='保存到: ' + self.save_dir)
        tk.Label(r2, textvariable=self.dir_var, bg=CARD, fg='#718096',
                 font=('Microsoft YaHei UI', 8), anchor='e').pack(side='right')
        ttk.Button(r2, text='更改目录', command=self.choose_dir).pack(side='right', padx=5)

        # 任务表
        wrap = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill='both', expand=True, padx=10, pady=(8, 4))
        inner = tk.Frame(wrap, bg=CARD, padx=6, pady=6)
        inner.pack(fill='both', expand=True)
        cols = ('name', 'kind', 'size', 'progress', 'speed', 'status')
        heads = {'name': '文件', 'kind': '类型', 'size': '大小', 'progress': '进度',
                 'speed': '速度', 'status': '状态'}
        widths = {'name': 380, 'kind': 60, 'size': 90, 'progress': 130, 'speed': 100, 'status': 110}
        self.tree = ttk.Treeview(inner, columns=cols, show='headings', selectmode='extended')
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor='w' if c == 'name' else 'center')
        vs = ttk.Scrollbar(inner, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        self.tree.tag_configure('ok', foreground=OKC)
        self.tree.tag_configure('err', foreground=ERRC)
        self.tree.tag_configure('run', foreground=ACCENT)
        self.tree.bind('<Double-1>', lambda e: self.resume_selected())

        self.progress = ttk.Progressbar(self, mode='determinate', maximum=100)
        self.progress.pack(fill='x', padx=10, pady=(0, 4))
        self.status = tk.StringVar(value='就绪')
        tk.Label(self, textvariable=self.status, bg=BG, fg='#4a5568',
                 font=('Microsoft YaHei UI', 9), anchor='w').pack(fill='x', padx=12, pady=(0, 4))

        # 日志/记录 页签
        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=False, padx=10, pady=(0, 8))
        logtab = tk.Frame(nb, bg=CARD)
        nb.add(logtab, text=' 下载日志 ')
        self.log_box = tk.Text(logtab, height=9, wrap='none', font=('Consolas', 9),
                               bg='#0f172a', fg='#cbd5e0', relief='flat', bd=0, padx=6, pady=4)
        self.log_box.pack(side='left', fill='both', expand=True)
        ls = ttk.Scrollbar(logtab, orient='vertical', command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=ls.set)
        ls.pack(side='left', fill='y')
        self._init_log_tags(self.log_box, self.log_box)
        logbar = tk.Frame(logtab, bg=CARD)
        logbar.pack(side='right', fill='y')
        ttk.Button(logbar, text='清空', style='Ghost.TButton',
                   command=self.clear_dl_log).pack(fill='x', pady=2)
        ttk.Button(logbar, text='打开文件', style='Ghost.TButton',
                   command=self.open_dl_log_file).pack(fill='x', pady=2)

        histtab = tk.Frame(nb, bg=CARD)
        nb.add(histtab, text=' 下载记录 ')
        hcols = ('time', 'name', 'result', 'size', 'path')
        hh = {'time': '时间', 'name': '文件', 'result': '结果', 'size': '大小', 'path': '保存位置'}
        self.hist = ttk.Treeview(histtab, columns=hcols, show='headings', selectmode='browse')
        for c in hcols:
            self.hist.heading(c, text=hh[c])
            self.hist.column(c, width={'time': 140, 'name': 300, 'result': 70, 'size': 90, 'path': 360}[c],
                             anchor='w' if c in ('name', 'path') else 'center')
        hv = ttk.Scrollbar(histtab, orient='vertical', command=self.hist.yview)
        self.hist.configure(yscrollcommand=hv.set)
        hb = tk.Frame(histtab, bg=CARD)
        hb.pack(side='right', fill='y')
        ttk.Button(hb, text='清空记录', command=self.clear_history).pack(fill='x', pady=2)
        ttk.Button(hb, text='打开位置', command=self.open_history_file).pack(fill='x', pady=2)
        self.hist.pack(side='left', fill='both', expand=True)
        hv.pack(side='left', fill='y')
        self._load_hist_view()
        self._poll()

    # ---------- 通用 ----------
    def _init_log_tags(self, widget, other=None):
        widget.tag_configure('info', foreground='#cbd5e0')
        widget.tag_configure('warn', foreground='#f6ad55')
        widget.tag_configure('err', foreground='#fc8181')
        widget.tag_configure('core', foreground='#90cdf4')

    def dlog(self, msg, tag='info'):
        # 线程安全：工作线程只入队，由主线程 _poll 写入控件
        self._logq.put((msg, tag))
        dl.dl_log(msg)

    def _drain_logq(self):
        while True:
            try:
                msg, tag = self._logq.get_nowait()
            except queue.Empty:
                break
            stamp = time.strftime('%H:%M:%S')
            try:
                self.log_box.insert('end', f"{stamp} {msg}\n", tag)
                total = int(self.log_box.index('end-1c').split('.')[0])
                if total > 4000:
                    self.log_box.delete('1.0', '1500.0')
                self.log_box.see('end')
            except Exception:
                pass

    def clear_dl_log(self):
        try:
            self.log_box.delete('1.0', 'end')
        except Exception:
            pass

    def open_dl_log_file(self):
        try:
            os.startfile(dl.DL_LOG_FILE)
        except Exception:
            tk.messagebox.showinfo('下载记录', f'日志文件：{dl.DL_LOG_FILE}', parent=self)

    def open_history_file(self):
        try:
            os.startfile(dl.HISTORY_FILE)
        except Exception:
            pass

    def _proxy(self, url=None):
        action = dl.rule_action(url, self.rules) if url else None
        if action == 'direct':
            return None, '规则:直连'
        if action == 'proxy':
            if self.app.core.running():
                return f"http://127.0.0.1:{self.app.cfg['http_port']}", '规则:代理'
            return None, '规则:代理(未连接,直连)'
        if not self._snap['proxy']:
            return None, '未勾选代理'
        if not self.app.core.running():
            return None, '未连接节点,直连'
        return f"http://127.0.0.1:{self.app.cfg['http_port']}", '代理'

    def choose_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.save_dir)
        if d:
            self.save_dir = d
            self.dir_var.set('保存到: ' + d)
            self.app.cfg['dl_dir'] = d
            core.save_config(self.app.cfg)

    def open_dir_btn(self):
        try:
            os.startfile(self.save_dir)
        except Exception:
            pass

    def _unique_path(self, name):
        base = dl.sanitize(name) or 'download.bin'
        p = os.path.join(self.save_dir, base)
        stem, ext = os.path.splitext(base)
        i = 1
        while os.path.exists(p):
            p = os.path.join(self.save_dir, f'{stem} ({i}){ext}')
            i += 1
        return p

    # ---------- 任务 ----------
    def _add_task(self, url, kind='http', name=None):
        if not name:
            name = dl.filename_from_url(url) or ('bt-task' if kind == 'bt' else 'download.bin')
            if kind == 'hls':
                name = os.path.splitext(name)[0] + '.ts'
        t = {'url': url, 'kind': kind, 'name': name, 'size': 0, 'done': 0, 'speed': 0,
             'status': '排队', 'stop': threading.Event(), 'out': '', 'created': time.strftime('%Y-%m-%d %H:%M:%S')}
        self.tasks.append(t)
        self._persist(force=True)
        return t

    def _row(self, idx):
        if not self.tree.exists(str(idx)):
            return
        t = self.tasks[idx]
        if t.get('kind') == 'hls' and t.get('segs_total'):
            pct = int(t.get('done', 0) * 100 / max(1, t['segs_total']))
            txt = f"{t.get('done', 0)}/{t['segs_total']} 片"
            size_txt = dl.human(t.get('size', 0)) if t.get('size') else '?'
        else:
            pct = int(t.get('done', 0) * 100 / t['size']) if t.get('size') else 0
            txt = f"{pct}%  {dl.human(t.get('done', 0))}/{dl.human(t.get('size', 0)) if t.get('size') else '?'}"
            size_txt = dl.human(t.get('size', 0)) if t.get('size') else '?'
        tag = ''
        if t['status'] == '完成':
            tag = 'ok'
        elif t['status'] in ('失败', '已中断'):
            tag = 'err'
        elif t['status'] in ('下载中', '探测中'):
            tag = 'run'
        self.tree.item(str(idx), values=(t['name'], t.get('kind', 'http'), size_txt,
                                         txt, f"{dl.human(t['speed'])}/s", t['status']), tags=(tag,) if tag else ())
        if idx == self.selected_index():
            self.progress['value'] = pct

    def selected_index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def selected_indexes(self):
        return [int(x) for x in self.tree.selection() if x.isdigit()]

    def start_direct(self):
        url = self.url_var.get().strip()
        if not url:
            tk.messagebox.showinfo('下载器', '请输入链接', parent=self)
            return
        if dl.is_magnet(url):
            self._add_task(url, 'bt')
        elif dl.is_hls(url):
            self._add_task(url, 'hls')
        elif url.startswith(('http://', 'https://')):
            self._add_task(url, 'http')
        else:
            tk.messagebox.showinfo('下载器', '只支持 http(s) 链接、m3u8 或 magnet 磁力链接', parent=self)
            return
        self.dlog(f'任务已加入: {url[:120]}')
        self._kick_worker()

    def analyze_page(self):
        url = self.url_var.get().strip()
        if not url.startswith(('http://', 'https://')):
            tk.messagebox.showinfo('下载器', '请输入网页地址', parent=self)
            return
        proxy, why = self._proxy(url)
        self.status.set(f'正在分析网页（{why}）...')

        def run():
            html = dl.fetch_page(url, proxy)
            links = dl.parse_page_links(html, url) if html else []
            self.after(0, lambda: self._show_links(url, links))

        threading.Thread(target=run, daemon=True).start()

    def _show_links(self, page_url, links):
        self.status.set(f'网页分析完成：发现 {len(links)} 个可下载链接')
        if not links:
            tk.messagebox.showinfo('分析网页', '没有识别到可下载的文件链接', parent=self)
            return
        win = tk.Toplevel(self)
        win.title('选择要下载的链接')
        win.geometry('860x540')
        win.transient(self)
        tk.Label(win, text=f'{page_url}\n共 {len(links)} 个链接（可多选）', justify='left',
                 font=('Microsoft YaHei UI', 9)).pack(anchor='w', padx=10, pady=6)
        box = tk.Frame(win)
        box.pack(fill='both', expand=True, padx=10)
        lb = tk.Listbox(box, selectmode='extended', font=('Consolas', 9))
        sb = ttk.Scrollbar(box, orient='vertical', command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        for u, text, ext in links:
            lb.insert('end', f'{(ext or "?"):8s} {text[:50]:50s} {u[:70]}')
        lb.selection_set(0, 'end')

        def do_dl():
            idxs = list(lb.curselection())
            if not idxs:
                return
            for i in idxs:
                u = links[i][0]
                kind = 'hls' if dl.is_hls(u) else ('bt' if dl.is_magnet(u) else 'http')
                self._add_task(u, kind)
            self.dlog(f'从网页加入 {len(idxs)} 个任务')
            self._kick_worker()
            win.destroy()

        bar = tk.Frame(win)
        bar.pack(fill='x', pady=8, padx=10)
        ttk.Button(bar, text='下载选中', style='Primary.TButton', command=do_dl).pack(side='right')
        ttk.Button(bar, text='全选', command=lambda: (lb.selection_clear(0, 'end'), lb.selection_set(0, 'end'))).pack(side='right', padx=6)
        ttk.Button(bar, text='取消', command=win.destroy).pack(side='right')

    def resume_selected(self):
        idxs = self.selected_indexes()
        if not idxs:
            return
        changed = False
        for i in idxs:
            if self.tasks[i]['status'] in ('失败', '已中断', '已取消'):
                self.tasks[i]['status'] = '排队'
                self.tasks[i]['stop'] = threading.Event()
                changed = True
        if changed:
            self.dlog(f'继续 {len(idxs)} 个任务')
            self._kick_worker()

    def cancel_selected(self):
        for i in self.selected_indexes():
            self.tasks[i]['stop'].set()
            self.tasks[i]['status'] = '取消中'
            self._row(i)

    def _kick_worker(self):
        if self.worker and self.worker.is_alive():
            return
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def _worker(self):
        while True:
            task = next((t for t in self.tasks if t['status'] == '排队'), None)
            if not task:
                return
            task['status'] = '探测中'
            proxy, why = self._proxy(task['url'])
            task['proxy_used'] = why
            self._persist(force=True)
            try:
                if task['kind'] == 'bt':
                    self._run_bt(task, proxy)
                    continue
                if task['kind'] == 'hls':
                    self._run_hls(task, proxy)
                    continue
                self._run_http(task, proxy)
            except Exception as e:
                task['status'] = '失败'
                self.dlog(f'任务异常: {task["name"]} - {e}', 'err')
                self._record(task, '失败')
            finally:
                self._persist(force=True)

    def _progress_cb(self, task):
        def cb(done, total, speed):
            task['done'] = done
            if task.get('kind') == 'hls':
                if total:
                    task['segs_total'] = total
            elif total:
                task['size'] = total
            task['speed'] = speed
        return cb

    def _run_http(self, task, proxy):
        dlog = self.dlog
        info = dl.probe(task['url'], proxy)
        if info.get('error'):
            task['status'] = '失败'
            dlog(f'探测失败: {task["url"][:80]} - {info["error"]}', 'err')
            self._record(task, '失败')
            return
        task['name'] = info.get('filename') or task['name']
        task['size'] = info.get('size') or 0
        task['out'] = self._unique_path(task['name'])
        task['status'] = '下载中'
        threads = max(1, int(self._snap['threads']))
        # 小文件用 curl 更省事；大文件用 aria2 多连接更稳
        small = task.get('size') and task['size'] < 4 * 1024 * 1024
        use_aria = bool(self._snap['aria']) and bool(dl.aria2_path()) and not small
        try:
            self.app.cfg['dl_backend'] = 'aria2' if use_aria else 'curl'
            core.save_config(self.app.cfg)
        except Exception:
            pass
        if use_aria:
            dlog(f"开始下载(aria2 {threads} 连接): {task['name']} ({dl.human(task['size'])}, {task.get('proxy_used','')})")
            ok, msg = dl.http_download_aria2(task['url'], task['out'], proxy=proxy,
                                             threads=threads,
                                             progress=self._progress_cb(task), stop=task['stop'])
            if not ok and not task['stop'].is_set():
                dlog(f'aria2 失败（{msg}），改用 curl 分段重试', 'warn')
                task['done'] = 0
                task['size'] = 0
                ok, msg = dl.download(task['url'], task['out'], proxy=proxy, threads=threads,
                                      progress=self._progress_cb(task), stop=task['stop'])
        else:
            dlog(f"开始下载: {task['name']} ({dl.human(task['size'])}, {threads} 线程, {task.get('proxy_used','')})")
            ok, msg = dl.download(task['url'], task['out'], proxy=proxy, threads=threads,
                                  progress=self._progress_cb(task), stop=task['stop'])
        task['status'] = '完成' if ok else ('已取消' if task['stop'].is_set() else '失败')
        if ok:
            dlog(f'✓ 下载完成: {task["out"]}')
            self._record(task, '完成')
        else:
            dlog(f'✗ 下载未完成: {task["name"]} - {msg}', 'err')
            if task['status'] == '失败':
                self._record(task, '失败')

    def _run_hls(self, task, proxy):
        if not task['name'].lower().endswith('.ts'):
            task['name'] = os.path.splitext(task['name'])[0] + '.ts'
        task['out'] = self._unique_path(task['name'])
        task['status'] = '下载中'
        self.dlog(f"开始 HLS 下载: {task['name']} ({task.get('proxy_used','')})")
        ok, msg = dl.hls_download(task['url'], task['out'], proxy=proxy,
                                  threads=max(1, int(self._snap['threads'])),
                                  progress=self._progress_cb(task), stop=task['stop'],
                                  log=lambda m: self.dlog(m))
        task['status'] = '完成' if ok else ('已取消' if task['stop'].is_set() else '失败')
        if ok:
            try:
                task['size'] = os.path.getsize(task['out'])
            except Exception:
                pass
            self.dlog(f'✓ HLS 下载完成: {task["out"]}')
            self._record(task, '完成')
        else:
            self.dlog(f'✗ HLS 下载未完成: {task["name"]} - {msg}', 'err')
            if task['status'] == '失败':
                self._record(task, '失败')

    def _run_bt(self, task, proxy):
        task['status'] = '下载中'
        self.dlog(f"开始 BT 下载: {task['url'][:120]} ({task.get('proxy_used','')})")
        ok, msg = dl.bt_download(task['url'], self.save_dir, proxy=proxy,
                                 progress=self._progress_cb(task), stop=task['stop'],
                                 log=lambda m: self.dlog(m, 'warn'))
        task['status'] = '完成' if ok else ('已取消' if task['stop'].is_set() else '失败')
        if ok:
            task['out'] = self.save_dir
            self.dlog(f'✓ BT 下载完成: {self.save_dir}')
            self._record(task, '完成')
        else:
            self.dlog(f'✗ BT 下载未完成: {msg}', 'err')
            if task['status'] == '失败':
                self._record(task, '失败')

    # ---------- 记录 ----------
    def _record(self, task, result):
        rec = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'name': task.get('name', ''),
               'result': result, 'size': task.get('size', 0), 'path': task.get('out', ''),
               'url': task.get('url', ''), 'kind': task.get('kind', 'http')}
        self.history.append(rec)
        if len(self.history) > 1000:
            self.history = self.history[-1000:]
        dl.save_history(self.history)
        self._load_hist_view()

    def _load_hist_view(self):
        try:
            self.hist.delete(*self.hist.get_children())
            for i, h in enumerate(reversed(self.history[-300:])):
                self.hist.insert('', 'end', iid=str(i),
                                 values=(h.get('time', ''), h.get('name', ''), h.get('result', ''),
                                         dl.human(h.get('size', 0)), h.get('path', '')))
        except Exception:
            pass

    def clear_history(self):
        self.history = []
        dl.save_history(self.history)
        self._load_hist_view()

    # ---------- 站点规则 ----------
    def edit_rules(self):
        win = tk.Toplevel(self)
        win.title('站点代理规则')
        win.geometry('720x480')
        win.transient(self)
        tk.Label(win, text='按域名决定是否走代理（支持通配符，如 *.example.com）。未命中的站点按主界面「走代理」勾选处理。',
                 wraplength=680, justify='left', font=('Microsoft YaHei UI', 9)).pack(anchor='w', padx=10, pady=6)
        wrap = tk.Frame(win)
        wrap.pack(fill='both', expand=True, padx=10)
        cols = ('pattern', 'action')
        tv = ttk.Treeview(wrap, columns=cols, show='headings', height=12)
        tv.heading('pattern', text='域名规则')
        tv.heading('action', text='动作')
        tv.column('pattern', width=480, anchor='w')
        tv.column('action', width=100, anchor='center')
        tv.pack(side='left', fill='both', expand=True)
        for r in self.rules:
            tv.insert('', 'end', values=(r.get('pattern', ''), r.get('action', '')))

        form = tk.Frame(win)
        form.pack(fill='x', padx=10, pady=8)
        pat_var = tk.StringVar()
        act_var = tk.StringVar(value='proxy')
        ttk.Entry(form, textvariable=pat_var, width=40).pack(side='left')
        ttk.Combobox(form, textvariable=act_var, values=('proxy', 'direct'), width=8,
                     state='readonly').pack(side='left', padx=6)

        def add():
            p = pat_var.get().strip()
            if not p:
                return
            tv.insert('', 'end', values=(p, act_var.get()))
            pat_var.set('')

        def delete():
            for iid in tv.selection():
                tv.delete(iid)

        def save():
            rules = []
            for iid in tv.get_children():
                v = tv.item(iid, 'values')
                rules.append({'pattern': v[0], 'action': v[1]})
            self.rules = rules
            dl.save_rules(rules)
            self.dlog(f'站点规则已保存（{len(rules)} 条）')
            win.destroy()

        ttk.Button(form, text='添加', command=add).pack(side='left')
        ttk.Button(form, text='删除', command=delete).pack(side='left', padx=6)
        bar = tk.Frame(win)
        bar.pack(fill='x', padx=10, pady=(0, 10))
        ttk.Button(bar, text='保存', style='Primary.TButton', command=save).pack(side='right')
        ttk.Button(bar, text='取消', command=win.destroy).pack(side='right', padx=6)

    # ---------- 持久化 / 轮询 ----------
    def _persist(self, force=False):
        now = time.time()
        if not force and now - self._last_save < 3:
            return
        self._last_save = now
        data = []
        for t in self.tasks:
            d = {k: v for k, v in t.items() if k != 'stop'}
            data.append(d)
        dl.save_tasks(data)

    def _poll(self):
        # 主线程读取控件状态，工作线程只用快照
        try:
            self._snap['proxy'] = bool(self.proxy_var.get())
            self._snap['threads'] = max(1, int(self.threads_var.get() or 8))
            self._snap['aria'] = bool(self.backend_var.get())
        except Exception:
            pass
        self._drain_logq()
        # 行数与任务数不一致时重建（加载持久化任务 / 新增任务）
        if len(self.tree.get_children()) != len(self.tasks):
            self.tree.delete(*self.tree.get_children())
            for i, t in enumerate(self.tasks):
                self.tree.insert('', 'end', iid=str(i), values=(
                    t.get('name', ''), t.get('kind', 'http'), '?', '0%', '—', t.get('status', '')))
        for i in range(len(self.tasks)):
            self._row(i)
        self._persist()
        try:
            if self.winfo_exists():
                self.after(700, self._poll)
        except Exception:
            pass

    def on_close(self):
        self._persist(force=True)
        super().destroy()


def main():
    core.kill_stale()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('SteadyLink.NodeManager.1')
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
