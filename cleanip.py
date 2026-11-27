"""CleanIP（cleanip.io）查询封装。

官方提供 MCP 接口：https://cleanip.io/mcp[/<API密钥>]
- 不带密钥：只返回归属地/ASN；
- 带密钥：返回纯净度评分、等级、IP 类型、原生/广播、风险标记、场景适配等。
免费账户每天可查 20 个不同 IP（https://cleanip.io/account 创建密钥）。
"""
import json
import os
import subprocess
import tempfile

MCP_URL = 'https://cleanip.io/mcp'
CREATE = 0x08000000
UA = 'SteadyLink/1.0 (+https://github.com/yhl4466/SteadyLink)'


def _curl(args, timeout=30):
    return subprocess.run(['curl.exe', '-sS', '-L', '--ssl-no-revoke', '--max-time', str(timeout),
                           '-A', UA] + args, capture_output=True, text=True,
                          timeout=timeout + 10, creationflags=CREATE, errors='replace')


def _parse_body(text):
    """兼容普通 JSON 与 SSE（event-stream）响应。"""
    text = (text or '').strip()
    if not text:
        return None
    if text.startswith('{'):
        try:
            return json.loads(text)
        except Exception:
            pass
    payload = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('data:'):
            chunk = line[5:].strip()
            if chunk and chunk != '[DONE]':
                payload = chunk
    if payload:
        try:
            return json.loads(payload)
        except Exception:
            return None
    return None


def endpoint(key=''):
    key = (key or '').strip()
    return f'{MCP_URL}/{key}' if key else MCP_URL


def rpc(method, params, key='', proxy=None, timeout=30):
    """调用一次 MCP JSON-RPC，返回 result 或 {'error': ...}。"""
    body = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(body, f, ensure_ascii=False)
        args = ['-X', 'POST', endpoint(key),
                '-H', 'Content-Type: application/json',
                '-H', 'Accept: application/json, text/event-stream',
                '--data-binary', '@' + path]
        if key:
            args += ['-H', f'Authorization: Bearer {key}']
        if proxy:
            args += ['-x', proxy]
        r = _curl(args, timeout=timeout)
        data = _parse_body(r.stdout)
        if not data:
            return {'error': (r.stderr or '空响应')[:200]}
        if 'error' in data:
            return {'error': str(data['error'])[:300]}
        return data.get('result') or {'error': '无 result'}
    except Exception as e:
        return {'error': str(e)[:200]}
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def check_ip(ip, key='', proxy=None, timeout=30):
    """查询单个 IP，返回结构化数据（官方字段原样保留）。

    返回 dict；失败时 {'error': ...}。无密钥时结果里没有 cleanip_score。
    """
    ip = (ip or '').strip()
    if not ip:
        return {'error': '缺少 IP'}
    result = rpc('tools/call', {'name': 'check_ip', 'arguments': {'ip': ip}},
                 key=key, proxy=proxy, timeout=timeout)
    if not result or 'error' in result:
        return result
    data = result.get('structuredContent')
    if not data:
        for item in result.get('content') or []:
            if item.get('type') == 'text':
                try:
                    data = json.loads(item.get('text') or '')
                    break
                except Exception:
                    pass
    return data or {'error': '返回内容为空'}


# ---------------- 统一结果 ----------------
def normalize(data, source='cleanip'):
    """把 CleanIP 返回整理成界面使用的统一结构（兼容多种字段命名）。"""
    d = data or {}
    score = d.get('cleanip_score')
    if score is None:
        score = d.get('score')
    if score is None:
        score = d.get('purity_score')
    grade = d.get('cleanip_grade') or d.get('grade') or d.get('purity_grade') or ''
    flags = d.get('flags') or {}
    native = d.get('native')
    if native is None and d.get('broadcast') is not None:
        native = not bool(d.get('broadcast'))
    if native is None:
        nb = (d.get('native_or_broadcast') or d.get('broadcast_type') or '')
        if isinstance(nb, str) and nb:
            native = nb.lower().startswith('native')
    findings = d.get('findings') or d.get('deductions') or d.get('risk_findings') or []
    scenarios = d.get('scenarios') or d.get('scenario_scores') or d.get('usage_scores') or {}
    return {
        'source': source,
        'score': int(score) if isinstance(score, (int, float)) else None,
        'grade': str(grade or ''),
        'ip_type': d.get('ip_type') or d.get('type') or '',
        'native': native,
        'broadcast': (d.get('broadcast') if d.get('broadcast') is not None
                      else (None if native is None else (not native))),
        'flags': {
            'proxy': bool(flags.get('proxy') or d.get('is_proxy')),
            'vpn': bool(flags.get('vpn') or d.get('is_vpn')),
            'tor': bool(flags.get('tor') or d.get('is_tor')),
            'hosting': bool(flags.get('hosting') or d.get('is_hosting')),
            'abuse': bool(flags.get('abuse') or d.get('is_abuser')),
        },
        'findings': findings if isinstance(findings, list) else [],
        'scenarios': scenarios if isinstance(scenarios, dict) else {},
        'location': d.get('location') or {},
        'network': d.get('network') or {},
        'report_url': d.get('report_url') or '',
        'note': d.get('note') or '',
        'coverage': d.get('coverage') or d.get('confidence') or '',
        'raw': d,
    }


def has_key(key=''):
    return bool((key or '').strip())


def full_check(ip, proxy=None, key='', timeout=30):
    """优先用 CleanIP 官方评分；失败或未配置密钥时回退本地估算。"""
    ip = (ip or '').strip()
    if has_key(key):
        data = check_ip(ip, key=key, proxy=proxy, timeout=timeout)
        if data and 'error' not in data:
            norm = normalize(data, 'cleanip')
            if norm.get('score') is not None:
                return norm
        # 官方失败：本地回退，但标注原因
        import ipscore
        local = ipscore.check(ip, proxy)
        local['note'] = 'CleanIP 查询失败或无评分，以下为本地估算：' + str(data.get('error') or '')[:120]
        return local
    import ipscore
    local = ipscore.check(ip, proxy)
    local['note'] = '未配置 CleanIP 密钥，以下为本地估算（免费数据源）'
    return local
