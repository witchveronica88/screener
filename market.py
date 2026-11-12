"""가격 받기 · 지표 · 노란 점 — 종가 수집(prices.py)과 상황판(desk.py)이 같이 쓰는 부분

판정 규칙이 아니라 계산 도구만 둔다. 자주 바뀌는 판정은 desk.py에 둔다.
그래서 desk.py가 깨져도 prices.py는 이 파일만으로 종가를 받는다.
"""
import urllib.request, urllib.parse, csv, io, json, os, time, datetime
from zoneinfo import ZoneInfo

UA = {'User-Agent': 'Mozilla/5.0'}
CACHE = '.cache'          # 같은 실행(워크플로 한 번) 안에서 prices.py가 받은 일봉을 desk.py가 다시 쓴다
CACHE_HOURS = 3
TARGET = 10               # 목표 수익 (%) — 과거 결과를 셀 때 쓰는 운영 설정
DIP_DAYS = 60             # 노란 점 뒤 목표 도달을 기다려 보는 기간 (거래일)


# ───────────── 자료 받기 ─────────────
def num(x):
    """숫자로 바꾸고, 비었거나 숫자가 아니면 None"""
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def confirmed(out):
    """장이 아직 안 끝난 오늘 봉은 확정 종가가 아니므로 뺀다 (장중에 실행된 경우).
       뉴욕 날짜가 오늘이고 16:15 전이면 마지막 봉을 뺀다"""
    ny = datetime.datetime.now(ZoneInfo('America/New_York'))
    if out and out[-1]['d'] == ny.date().isoformat() and (ny.hour, ny.minute) < (16, 15):
        out = out[:-1]
    return out


def _yahoo(sym):
    # range=max는 월봉으로 바뀌므로 period1=0으로 전체 일봉을 받는다
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}'
           f'?period1=0&period2={int(time.time())}&interval=1d')
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.load(r)
    res = j['chart']['result'][0]
    q = res['indicators']['quote'][0]
    cl = q['close']
    vo = q.get('volume') or [None] * len(cl)
    # 수정종가(배당 반영) — 기초자산 대비 수익률 비교에만 쓴다
    ad = ((res['indicators'].get('adjclose') or [{}])[0].get('adjclose')) or cl
    nz = lambda x, c: x if x is not None else c
    return [{'d': datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat(),
             'o': nz(o, c), 'h': nz(h, c), 'l': nz(l, c), 'c': c, 'v': num(v), 'a': nz(a, c)}
            for t, c, v, a, o, h, l in zip(res['timestamp'], cl, vo, ad, q['open'], q['high'], q['low'])
            if c is not None]


_stooq_fail = 0           # Stooq가 연달아 실패하면 그날은 건너뛴다 (시간 초과로 수집이 몇십 분씩 늘어지는 것 방지)


def _stooq(sym):
    global _stooq_fail
    if _stooq_fail >= 3 or sym.startswith('^'):
        return []
    try:
        req = urllib.request.Request(f'https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d', headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode('utf-8', 'replace')
        rows = [r for r in csv.DictReader(io.StringIO(txt)) if r.get('Close') not in (None, '', 'N/A')]
        fl = lambda r, k: num(r.get(k)) or float(r['Close'])
        out = [{'d': r['Date'], 'o': fl(r, 'Open'), 'h': fl(r, 'High'), 'l': fl(r, 'Low'),
                'c': float(r['Close']), 'v': num(r.get('Volume')), 'a': float(r['Close'])} for r in rows]
        _stooq_fail = 0
        return out if len(out) >= 30 else []
    except Exception as e:
        print(f'  stooq 실패 {sym}: {e}')
        _stooq_fail += 1
        if _stooq_fail >= 3:
            print('  stooq 3회 연속 실패 — 이번 실행에서는 건너뜀')
        return []


def _cache_path(sym):
    return os.path.join(CACHE, sym.replace('^', '_') + '.json')


def fetch(sym):
    """일봉 [{d,o,h,l,c,v,a}] (오래된 순, 확정 봉만). Yahoo 두 번 → 실패하면 Stooq. 다 실패하면 빈 리스트.
       o·h·l이 비어 있으면 종가로 채운다. a는 수정종가, v는 거래량(없으면 None)"""
    p = _cache_path(sym)
    try:
        if time.time() - os.path.getmtime(p) < CACHE_HOURS * 3600:
            return confirmed(json.load(open(p, encoding='utf-8')))
    except (OSError, ValueError):
        pass
    out = []
    for attempt in range(2):   # 가끔 연결이 끊겨 한 번 더 시도한다
        try:
            out = _yahoo(sym)
            if out:
                break
        except Exception as e:
            print(f'  yahoo 실패 {sym} ({attempt + 1}/2): {e}')
        time.sleep(2)
    out = confirmed(out or _stooq(sym))
    if out:
        try:
            os.makedirs(CACHE, exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(out, f)
        except OSError:
            pass
    return out


# ───────────── 지표 ─────────────
def sma(v, n):
    out = [None] * len(v); s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n: s -= v[i-n]
        if i >= n-1: out[i] = s / n
    return out


def stoch_k(B, n=5):
    """평활하지 않은 Fast %K"""
    out = [None] * len(B)
    for i in range(n-1, len(B)):
        hh = max(b['h'] for b in B[i-n+1:i+1]); ll = min(b['l'] for b in B[i-n+1:i+1])
        out[i] = 50.0 if hh == ll else (B[i]['c'] - ll) / (hh - ll) * 100
    return out


def cci(B, n=20):
    tp = [(b['h'] + b['l'] + b['c']) / 3 for b in B]
    out = [None] * len(B)
    for i in range(n-1, len(B)):
        w = tp[i-n+1:i+1]; m = sum(w) / n
        md = sum(abs(x - m) for x in w) / n
        out[i] = 0.0 if md == 0 else (tp[i] - m) / (0.015 * md)
    return out


def rsi_w(c, n=14):
    """Wilder RSI"""
    out = [None] * len(c)
    if len(c) < n + 1:
        return out
    d = [c[i] - c[i-1] for i in range(1, len(c))]
    g = sum(x for x in d[:n] if x > 0) / n
    l = sum(-x for x in d[:n] if x < 0) / n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n, len(d)):
        g = (g * (n-1) + max(d[i], 0)) / n
        l = (l * (n-1) + max(-d[i], 0)) / n
        out[i+1] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def boll(c, n=20, k=2):
    m = sma(c, n); up = [None] * len(c); lo = [None] * len(c)
    for i in range(n-1, len(c)):
        w = c[i-n+1:i+1]
        sd = (sum((x - m[i]) ** 2 for x in w) / n) ** 0.5
        up[i] = m[i] + k * sd; lo[i] = m[i] - k * sd
    return m, up, lo


def indicators(B):
    c = [b['c'] for b in B]
    k = stoch_k(B)
    kd = [None] * len(B)
    for i in range(len(B)):
        if i >= 2 and None not in k[i-2:i+1]:
            kd[i] = sum(k[i-2:i+1]) / 3
    m50 = sma(c, 50)
    bm, bu, bl = boll(c)
    return {'k': k, 'd': kd, 'cci': cci(B), 'rsi': rsi_w(c), 'bu': bu, 'bl': bl, 'm50': m50,
            'disp': [c[i] / m50[i] * 100 if m50[i] else None for i in range(len(c))]}


# ───────────── 노란 점 ─────────────
def is_dip(I, i):
    """노란 점: 세 지표가 모두 '많이 빠짐' (%K ≤ 20 · CCI ≤ -100 · RSI ≤ 30)"""
    k, cc, rs = I['k'][i], I['cci'][i], I['rsi'][i]
    return None not in (k, cc, rs) and k <= 20 and cc <= -100 and rs <= 30


def dip_past(B, I, P):
    """과거 노란 점 결과. 며칠 연달아 켜진 노란 점은 한 번으로 묶고, 묶음의 첫날 다음 거래일 시가에
       관찰 상품(P)을 샀다고 가정한다. DIP_DAYS 안에 장중 고가가 목표에 닿았는지,
       닿기 전 가장 깊은 하락, 못 닿았을 때 기간이 끝난 날 종가 기준 손익을 센다"""
    pidx = {b['d']: i for i, b in enumerate(P)}
    rows = []
    for i in range(len(B)):
        if not is_dip(I, i) or (i > 0 and is_dip(I, i - 1)):
            continue                                   # 묶음의 첫날만
        pi = pidx.get(B[i]['d'])
        if pi is None or pi + 1 >= len(P):
            continue
        entry = P[pi + 1]['o']; mae = 0.0; hit = None
        end = min(pi + DIP_DAYS, len(P) - 1)
        for j in range(pi + 1, end + 1):
            mae = min(mae, P[j]['l'] / entry - 1)
            if P[j]['h'] >= entry * (1 + TARGET / 100):
                hit = j - pi
                break
        if hit is None and end - pi < DIP_DAYS:
            continue                                   # 아직 기간이 안 끝난 최근 건은 세지 않는다
        rows.append((hit, mae * 100, None if hit else (P[end]['c'] / entry - 1) * 100))
    if not rows:
        return None
    hits = sorted(h for h, _, _ in rows if h is not None)
    miss = [m for h, _, m in rows if h is None]
    med = lambda a: (a[(len(a) - 1) // 2] + a[len(a) // 2]) / 2
    return {'n': len(rows), 'ep': len(rows), 'from': P[0]['d'], 'days': DIP_DAYS,
            'hit': round(len(hits) / len(rows) * 100),
            'medD': round(med(hits)) if hits else None,
            'maeAvg': round(sum(m for _, m, _ in rows) / len(rows), 1),
            'maeMax': round(min(m for _, m, _ in rows), 1),
            'miss': len(miss), 'missAvg': round(sum(miss) / len(miss), 1) if miss else None}


def yellow(B, P=None, base=None):
    """종목 카드의 노란 점. B: 판정에 쓰는 일봉. P가 있으면 B는 기초자산이고 P가 실제로 사는 상품이다
       (상황판의 SOXX→SOXL과 같은 방식). 종가가 고가·저가 밖으로 나간 봉은 범위를 종가까지 넓혀 계산한다"""
    fix = lambda b: {**b, 'h': max(b['h'], b['c']), 'l': min(b['l'], b['c'])}
    B = [fix(b) for b in B]
    if P:
        B = [b for b in B if b['d'] <= P[-1]['d']]
    P = [fix(b) for b in P if b['d'] <= B[-1]['d']] if P and B else B
    if len(B) < 30 or not P or P[-1]['d'] != B[-1]['d']:
        return None
    I = indicators(B); last = len(B) - 1
    on = is_dip(I, last); run = 0
    while on and run <= last and is_dip(I, last - run):
        run += 1
    li = next((i for i in range(last, max(last - 250, 0), -1) if is_dip(I, i)), None)
    r = {'on': on, 'run': run, 'k': round(I['k'][-1], 1), 'cci': round(I['cci'][-1], 1),
         'rsi': round(I['rsi'][-1], 1), 'past': dip_past(B, I, P)}
    if base:
        r['base'] = base
    # 종목 상세의 작은 그래프: 최근 3개월 종가와 노란 점이 찍힌 날(기초자산으로 판정했으면 그 날짜를 상품 가격 위에 표시)
    dots = {B[i]['d'] for i in range(max(last - 70, 0), last + 1) if is_dip(I, i)}
    w = P[-63:]
    r['chart'] = {'from': w[0]['d'], 'to': w[-1]['d'], 'c': [round(x['c'], 2) for x in w],
                  'dot': [i for i, x in enumerate(w) if x['d'] in dots]}
    if li is not None:
        d = B[li]['d']
        r['last'] = {'date': d, 'age': last - li, 'mv': round((B[-1]['c'] / B[li]['c'] - 1) * 100, 1)}
        pc = next((x['c'] for x in P if x['d'] == d), None) if P is not B else None
        if pc:
            r['last']['pmv'] = round((P[-1]['c'] / pc - 1) * 100, 1)
    return r


# ───────────── 회복 여부 ─────────────
REC_MIN_BARS = 750        # 3년치가 안 되면 '이력부족'


def recovery(B, by=None):
    """역대 고점 대비 지금 위치로 회복 여부를 정한다. −10% 이내 회복, −40% 이내 부분회복, 그 아래 미회복.
       레버리지 ETF는 가치가 깎여 자기 고점으로는 거의 못 돌아가므로 기초자산 일봉(B)으로 판정한다"""
    if not B:
        return None
    c = [b['c'] for b in B]
    dd = (c[-1] / max(c) - 1) * 100
    v = ('이력부족' if len(c) < REC_MIN_BARS else
         '회복' if dd >= -10 else '부분회복' if dd >= -40 else '미회복')
    r = {'v': v, 'dd': round(dd, 1), 'bars': len(c)}
    if by:
        r['by'] = by
    return r
