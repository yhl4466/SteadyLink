"""本地 IP 纯净度估算（未配置 CleanIP 密钥时的回退方案）。

评分思路参考 CleanIP 公开的评分规则：
- 0–100，越高越干净；等级 91+ A+ / 81+ A / 66+ B / 46+ C / 21+ D / 其余 F；
- 机房（IDC）地址最终分上限 75；
- 代理 / VPN / Tor、滥用黑名单、滥用记录会扣分；同组只取最重项；
- 数据源缺失只影响“可加分项”，不做无依据扣分。

与官方差异：官方使用商业数据库、/24 邻居扫描与自建观测，本模块使用免费公开数据，
分数仅作参考，界面会标注「本地估算」。
"""
import concurrent.futures as cf
import json
import os
import socket
import time

from cleanip import _curl

DNSBL = [
    ('zen.spamhaus.org', 'Spamhaus ZEN'),
    ('bl.spamcop.net', 'SpamCop'),
    ('b.barracudacentral.org', 'Barracuda'),
    ('dnsbl.sorbs.net', 'SORBS'),
    ('psbl.surriel.com', 'PSBL'),
    ('all.s5h.net', 's5h'),
    ('dnsbl-1.uceprotect.net', 'UCEPROTECT-L1'),
    ('spam.dnsbl.sorbs.net', 'SORBS-SMTP'),
]
EMAIL_ONLY = {'spam.dnsbl.sorbs.net'}

HOSTING_WORDS = (
    'vps', 'server', 'hosting', 'cloud', 'datacenter', 'data center', 'colo',
    'dedicated', 'virtual', 'amazon', 'aws', 'google', 'microsoft', 'azure',
    'digitalocean', 'linode', 'hetzner', 'ovh', 'contabo', 'vultr', 'alibaba',
    'aliyun', 'tencent', 'oracle', 'leaseweb', 'm247', 'packet', 'velia',
    'cdn', 'host', 'ipv4', 'ipv6',
)


def _get_json(url, proxy, timeout=8, retries=2):
    for _ in range(max(1, retries)):
        try:
            r = _curl(['--max-time', str(timeout), '-x', proxy, url] if proxy else
                      ['--max-time', str(timeout), url], timeout=timeout + 4)
            data = json.loads(r.stdout or '{}')
            if data:
                return data
        except Exception:
            pass
    return {}


def _dnsbl_hits(ip):
    rev = '.'.join(reversed(ip.split('.')))
    hits = []
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(2.5)
    try:
        for zone, name in DNSBL:
            try:
                socket.gethostbyname(f'{rev}.{zone}')
                hits.append({'zone': zone, 'name': name})
            except Exception:
                continue
    finally:
        socket.setdefaulttimeout(old)
    return hits


def collect(ip, proxy=None):
    """采集免费来源的数据（每个来源失败不影响其它来源）。"""
    ev = {'ip': ip, 'sources': [], 'errors': []}
    a = _get_json(f'http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,'
                  f'city,isp,org,as,asname,proxy,hosting,mobile,timezone,reverse', proxy)
    if a.get('status') == 'success':
        ev['ipapi'] = a
        ev['sources'].append('ip-api')
    else:
        ev['errors'].append('ip-api')
    b = _get_json(f'https://ipapi.is/json/?q={ip}', proxy, timeout=10)
    if b:
        ev['ipapi_is'] = b
        ev['sources'].append('ipapi.is')
    else:
        ev['errors'].append('ipapi.is')
    try:
        reverse = socket.gethostbyaddr(ip)[0]
    except Exception:
        reverse = ''
    ev['ptr'] = reverse
    ev['dnsbl'] = _dnsbl_hits(ip)
    ev['sources'].append('DNSBL')
    return ev


def _is_hosting(ev):
    a = ev.get('ipapi') or {}
    b = ev.get('ipapi_is') or {}
    if a.get('hosting') or b.get('is_datacenter'):
        return True
    asn_type = ((b.get('asn') or {}).get('type') or '').lower()
    company_type = ((b.get('company') or {}).get('type') or '').lower()
    if asn_type in ('hosting', 'business') or company_type in ('hosting', 'business'):
        return True
    text = ' '.join(str(x) for x in (a.get('isp'), a.get('org'), a.get('as'),
                                     b.get('asn', {}).get('name') if isinstance(b.get('asn'), dict) else '',
                                     ev.get('ptr') or '')).lower()
    return any(w in text for w in HOSTING_WORDS)


def _flags(ev):
    a = ev.get('ipapi') or {}
    b = ev.get('ipapi_is') or {}
    return {
        'proxy': bool(a.get('proxy') or b.get('is_proxy')),
        'vpn': bool(b.get('is_vpn')),
        'tor': bool(b.get('is_tor') or 'tor' in (b.get('company', {}) or {}).get('type', '').lower()),
        'hosting': _is_hosting(ev),
        'abuse': bool(b.get('is_abuser')),
        'crawler': bool(b.get('is_crawler')),
    }


def grade_of(score):
    if score >= 91:
        return 'A+'
    if score >= 81:
        return 'A'
    if score >= 66:
        return 'B'
    if score >= 46:
        return 'C'
    if score >= 21:
        return 'D'
    return 'F'


def score(ev):
    """根据采集结果打分，返回与 CleanIP 统一的字段结构。"""
    flags = _flags(ev)
    findings = []
    pts = 100

    def add(code, desc, delta):
        nonlocal pts
        pts += delta
        findings.append({'code': code, 'desc': desc, 'delta': delta})

    if flags['tor']:
        add('tor', 'Tor 出口（评分显著受限）', -45)
    elif flags['vpn']:
        add('vpn', '被数据源标记为 VPN 出口', -25)
    elif flags['proxy']:
        add('proxy', '被数据源标记为代理', -20)

    spam = [h for h in ev.get('dnsbl') or [] if h['zone'] not in EMAIL_ONLY]
    email = [h for h in ev.get('dnsbl') or [] if h['zone'] in EMAIL_ONLY]
    if spam:
        add('blacklist', f'命中 {len(spam)} 个滥用黑名单：'
                         + '、'.join(h['name'] for h in spam[:3]), -12 * min(len(spam), 3))
    if email:
        add('email_blacklist', '命中邮件信誉名单（只扣一次）', -5)
    if flags['abuse']:
        add('abuse', '有历史滥用记录', -15)
    if flags['crawler']:
        add('crawler', '被标记为爬虫/机器人流量', -5)

    if flags['hosting']:
        findings.append({'code': 'idc', 'desc': '机房/IDC 地址，最终分上限 75', 'delta': 0})
        pts = min(pts, 75)

    a0 = ev.get('ipapi') or {}
    b0 = ev.get('ipapi_is') or {}
    have_geo = bool(a0) or bool(b0)
    if not have_geo:
        return {
            'source': 'local', 'score': None, 'grade': '待评估', 'ip_type': '未知',
            'native': None, 'broadcast': None, 'flags': flags, 'findings': findings,
            'scenarios': {}, 'location': {}, 'network': {},
            'report_url': f'https://cleanip.io/{ev.get("ip", "")}',
            'coverage': '关键数据源不可用',
            'note': 'ip-api 与 ipapi.is 均不可用，证据不足，暂不评分（可重试或配置 CleanIP 密钥）',
            'raw': ev,
        }
    if not a0 or not b0:
        pts = min(pts, 70)
        findings.append({'code': 'coverage', 'desc': '部分数据源不可用，评分置信度较低（上限 70）',
                         'delta': 0})
    pts = max(0, min(100, pts))
    ip_type = ('机房/IDC' if flags['hosting'] else
               ('Tor' if flags['tor'] else ('VPN' if flags['vpn'] else
                                            ('代理' if flags['proxy'] else
                                             ('移动网络' if (ev.get('ipapi') or {}).get('mobile') else '未知')))))
    a = ev.get('ipapi') or {}
    b = ev.get('ipapi_is') or {}
    return {
        'source': 'local',
        'score': int(pts),
        'grade': grade_of(pts),
        'ip_type': ip_type,
        'native': None,
        'broadcast': None,
        'flags': flags,
        'findings': findings,
        'scenarios': {},
        'location': {
            'country': a.get('country') or '', 'country_code': a.get('countryCode') or '',
            'region': a.get('regionName') or '', 'city': a.get('city') or '',
            'timezone': a.get('timezone') or '',
        },
        'network': {
            'asn': b.get('asn', {}).get('asn') if isinstance(b.get('asn'), dict) else None,
            'as_name': (b.get('asn') or {}).get('name') if isinstance(b.get('asn'), dict)
            else (a.get('as') or ''),
            'isp': a.get('isp') or ((b.get('company') or {}).get('name') or ''),
        },
        'report_url': f'https://cleanip.io/{ev.get("ip", "")}',
        'coverage': f'{len(ev.get("sources") or [])} 个来源'
                    + (f'（缺失：{", ".join(ev.get("errors") or [])}）' if ev.get('errors') else ''),
        'raw': ev,
    }


HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'IP评分历史.json')


def load_history():
    try:
        with open(HISTORY_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(hist):
    try:
        tmp = HISTORY_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(hist[-800:], f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
    except Exception:
        pass


def record_score(ip, score, grade, source='local'):
    """记录一次评分，返回与上一次（同 IP、同来源）的对比。"""
    hist = load_history()
    prev = None
    for rec in reversed(hist):
        if (rec.get('ip') == ip and rec.get('source') == source
                and rec.get('score') is not None):
            prev = rec
            break
    rec = {'ip': ip, 'score': score, 'grade': grade, 'source': source,
           'ts': time.time(), 'time': time.strftime('%Y-%m-%d %H:%M:%S')}
    hist.append(rec)
    save_history(hist)
    out = {'prev_score': None, 'prev_grade': None, 'prev_time': None, 'delta': None}
    if prev:
        out.update(prev_score=prev.get('score'), prev_grade=prev.get('grade'),
                   prev_time=prev.get('time'))
        if score is not None and prev.get('score') is not None:
            out['delta'] = int(score) - int(prev['score'])
    return out


def check(ip, proxy=None):
    return score(collect(ip, proxy))
