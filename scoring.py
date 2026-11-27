"""批量 IP 评分：给机场里所有节点已有的出口 IP 打分并写回节点。"""
import concurrent.futures as cf
import threading
import time

import cleanip


def score_nodes(nodes, official=False, key='', proxy=None, workers=4,
                progress=None, stop=None):
    """并发给每个有出口 IP 的节点评分。

    official=True 时使用 CleanIP 官方评分（消耗配额），否则本地估算。
    结果写回节点字段：score / grade / score_source / score_type /
    score_flags / score_note / score_time。
    返回统计 dict。
    """
    stop = stop or threading.Event()
    tasks = []
    for i, n in enumerate(nodes):
        ip = str(n.get('ip') or '').strip()
        if ip:
            tasks.append((i, n, ip))
    skipped = len(nodes) - len(tasks)
    total = len(tasks)
    done = 0
    scored = failed = 0
    lock = threading.Lock()

    def work(item):
        i, n, ip = item
        if stop.is_set():
            return i, n, ip, {'error': 'cancelled'}
        try:
            r = cleanip.full_check(ip, proxy=proxy, key=(key if official else ''))
        except Exception as e:
            r = {'error': str(e)[:120]}
        return i, n, ip, r or {'error': 'empty'}

    with cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futures = [ex.submit(work, t) for t in tasks]
        for fut in cf.as_completed(futures):
            if stop.is_set():
                break
            try:
                i, n, ip, r = fut.result()
            except Exception as e:
                n = None
                r = {'error': str(e)[:120]}
            with lock:
                done += 1
            if n is not None:
                if r.get('score') is not None:
                    n['score'] = r['score']
                    n['grade'] = r.get('grade') or ''
                    n['score_source'] = r.get('source') or ('cleanip' if official else 'local')
                    n['score_type'] = r.get('ip_type') or ''
                    n['score_flags'] = r.get('flags') or {}
                    n['score_note'] = r.get('note') or ''
                    n['score_time'] = time.time()
                    scored += 1
                else:
                    n['score_note'] = r.get('error') or r.get('note') or '评分失败'
                    failed += 1
            if progress:
                try:
                    progress(done, total, n, r)
                except Exception:
                    pass
    return {'total': total, 'done': done, 'scored': scored,
            'failed': failed, 'skipped': skipped, 'stopped': stop.is_set()}
