"""가격 받기 · 지표 · 옐로우닷 — 종가 수집(prices.py)과 상황판(desk.py)이 같이 쓰는 부분

판정 규칙이 아니라 계산 도구만 둔다. 자주 바뀌는 판정은 desk.py에 둔다.
그래서 desk.py가 깨져도 prices.py는 이 파일만으로 종가를 받는다.
"""
import statistics
import urllib.request, urllib.parse, csv, io, json, os, time, datetime
from zoneinfo import ZoneInfo

UA = {'User-Agent': 'Mozilla/5.0'}
CACHE = '.cache'          # 같은 실행(워크플로 한 번) 안에서 prices.py가 받은 일봉을 desk.py가 다시 쓴다
CACHE_HOURS = 3
TARGET = 10               # 목표 수익 (%) — 과거 결과를 셀 때 쓰는 운영 설정
TARGET2 = 20              # 두 번째 목표 (%) — 같은 방식으로 함께 센다
DIP_DAYS = 60             # 옐로우닷 뒤 목표 도달을 기다려 보는 기간 (거래일)
DIP_FAST = 20             # 짧게 보는 기간 — +10%가 이 안에 닿았는지도 따로 센다 (화면 추천순의 기준)
# 옐로우업다운(옐로우닷 다음 날 1포트로 시작) — 운영 설정. 2026-11 백테스트로 정함(CLAUDE.md '옐로우업다운' 참고)
UD_POTS = 10              # 예비 자금 한 칸을 몇 포트로 나누나
UD_D = 0.05               # 매수 LOC: LP × (1 − UD_D − 보유 포트 × UD_STEP)   원래 규칙은 0
UD_U = 0.10               # 매도 LOC: LP × (1 + UD_U − 보유 포트 × UD_STEP)   원래 규칙은 0.02
UD_STEP = 0.002
UD_COST = 0.001           # 매매마다 비용 0.1%
UD_MAXD = 250             # 이 거래일 안에 안 끝나면 그날 종가로 평가(못 끝남)


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


FILLED = []              # 일봉이 아직 안 올라와 시세 요약으로 마지막 하루를 채운 종목 (로그용)


def _gap_days(a, b):
    """a와 b 사이(둘 다 빼고)의 평일 수"""
    d0, d1 = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
    return sum(1 for k in range(1, (d1 - d0).days) if (d0 + datetime.timedelta(k)).weekday() < 5)


def _intraday_open(sym, day):
    """그날 1시간봉의 첫 시가 (실패하면 None)"""
    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1d&interval=60m'
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
            res = json.load(r)['chart']['result'][0]
        for t, o in zip(res['timestamp'], res['indicators']['quote'][0]['open']):
            if o is not None and datetime.datetime.fromtimestamp(t, ZoneInfo('America/New_York')).date().isoformat() == day:
                return o
    except Exception:
        pass
    return None


def _fill_last(sym, res, out):
    """야후는 장이 끝나고 몇 시간 동안 그날 일봉을 안 올려 두는 일이 잦다(2026-11 확인: 마감 5시간 뒤에도
       62종목 중 40종목이 빠짐). 그동안에도 응답의 시세 요약(meta)에는 그날 종가·고가·저가·거래량이 있으므로
       마지막 하루만 그걸로 채운다. 시가는 그날 1시간봉의 첫 값. 사이에 빠진 평일이 있으면 채우지 않는다.
       장중이면 confirmed()가 다시 뺀다. 다음 실행 때 진짜 일봉으로 바뀐다"""
    m = res.get('meta') or {}
    rt, px = m.get('regularMarketTime'), num(m.get('regularMarketPrice'))
    if not (out and rt and px):
        return out
    day = datetime.datetime.fromtimestamp(rt, ZoneInfo('America/New_York')).date().isoformat()
    if day <= out[-1]['d'] or _gap_days(out[-1]['d'], day):
        return out
    hi, lo = num(m.get('regularMarketDayHigh')) or px, num(m.get('regularMarketDayLow')) or px
    o = num(_intraday_open(sym, day)) or px
    FILLED.append(sym)
    return out + [{'d': day, 'o': o, 'h': max(hi, o, px), 'l': min(lo, o, px), 'c': px,
                   'v': num(m.get('regularMarketVolume')), 'a': px}]


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
    out = [{'d': datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat(),
             'o': nz(o, c), 'h': nz(h, c), 'l': nz(l, c), 'c': c, 'v': num(v), 'a': nz(a, c)}
            for t, c, v, a, o, h, l in zip(res['timestamp'], cl, vo, ad, q['open'], q['high'], q['low'])
            if c is not None]
    return _fill_last(sym, res, out)


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


# ───────────── 옐로우닷 ─────────────
def is_dip(I, i):
    """옐로우닷: 세 지표가 모두 '많이 빠짐' (%K ≤ 20 · CCI ≤ -100 · RSI ≤ 30)"""
    k, cc, rs = I['k'][i], I['cci'][i], I['rsi'][i]
    return None not in (k, cc, rs) and k <= 20 and cc <= -100 and rs <= 30


def dip_past(B, I, P):
    """과거 옐로우닷 결과. 며칠 연달아 켜진 옐로우닷은 한 번으로 묶고, 묶음의 첫날 다음 거래일 시가에
       관찰 상품(P)을 샀다고 가정한다. DIP_DAYS 안에 장중 고가가 목표에 닿았는지,
       닿기 전 가장 깊은 하락, 못 닿았을 때 기간이 끝난 날 종가 기준 손익을 센다.
       두 번째 목표(TARGET2)는 닿은 비율·걸린 날만 센다. 기간이 안 끝났고 아직 못 닿은 건은 그 비율에서 뺀다.
       짧은 기간(DIP_FAST) 안에 +10%에 닿았는지도 따로 센다(hitF). 20거래일이 지났으면 60거래일이 안 끝났어도 센다"""
    pidx = {b['d']: i for i, b in enumerate(P)}
    rows = []
    fast = []                                          # (DIP_FAST 안에 닿음, 못 닿았으면 DIP_FAST 뒤 종가 손익 %)
    for i in range(len(B)):
        if not is_dip(I, i) or (i > 0 and is_dip(I, i - 1)):
            continue                                   # 묶음의 첫날만
        pi = pidx.get(B[i]['d'])
        if pi is None or pi + 1 >= len(P):
            continue
        entry = P[pi + 1]['o']; mae = 0.0; hit = hit2 = None
        end = min(pi + DIP_DAYS, len(P) - 1)
        for j in range(pi + 1, end + 1):
            if hit is None:
                mae = min(mae, P[j]['l'] / entry - 1)
                if P[j]['h'] >= entry * (1 + TARGET / 100):
                    hit = j - pi
            if P[j]['h'] >= entry * (1 + TARGET2 / 100):
                hit2 = j - pi
                break
        okF = hit is not None and hit <= DIP_FAST
        if okF or pi + DIP_FAST <= len(P) - 1:
            fast.append((okF, None if okF else (P[pi + DIP_FAST]['c'] / entry - 1) * 100))
        done = end - pi >= DIP_DAYS
        if hit is None and not done:
            continue                                   # 아직 기간이 안 끝난 최근 건은 세지 않는다
        rows.append((hit, mae * 100, None if hit else (P[end]['c'] / entry - 1) * 100, hit2, done))
    if not rows:
        return None
    hits = sorted(r[0] for r in rows if r[0] is not None)
    miss = [r[2] for r in rows if r[0] is None]
    r2 = [r[3] for r in rows if r[3] is not None or r[4]]          # 두 번째 목표를 셀 수 있는 건
    h2 = sorted(h for h in r2 if h is not None)
    med = lambda a: (a[(len(a) - 1) // 2] + a[len(a) // 2]) / 2
    return {'n': len(rows), 'ep': len(rows), 'from': P[0]['d'], 'days': DIP_DAYS,
            'hit': round(len(hits) / len(rows) * 100),
            'medD': round(med(hits)) if hits else None,
            'maeAvg': round(sum(r[1] for r in rows) / len(rows), 1),
            'maeMax': round(min(r[1] for r in rows), 1),
            'hit20': round(len(h2) / len(r2) * 100) if r2 else None, 'n20': len(r2),
            'medD20': round(med(h2)) if h2 else None,
            'miss': len(miss), 'missAvg': round(sum(miss) / len(miss), 1) if miss else None,
            'daysF': DIP_FAST, 'nF': len(fast),
            'hitF': round(sum(1 for f in fast if f[0]) / len(fast) * 100) if fast else None,
            'missF': sum(1 for f in fast if not f[0]),
            'missAvgF': round(statistics.fmean(f[1] for f in fast if not f[0]), 1) if any(not f[0] for f in fast) else None}


def dip_targets(B, I, P, since):
    """그래프에 그릴 옐로우닷 목표 — dip_past와 같은 셈법: 묶음의 첫날 다음 거래일 시가에 P를 샀다고 보고,
       DIP_DAYS 안에 장중 고가가 +TARGET%(목표가)에 닿은 첫날. 기간 끝이 since 이후인 것만.
       반환 [{d0 옐로우닷 날, buy 산 날, tgt 목표가, hit 닿은 날(없으면 None), end 닿은 날·기간 끝·마지막 날, open 아직 기다리는 중}]"""
    pidx = {b['d']: i for i, b in enumerate(P)}
    out = []
    for i in range(len(B)):
        if not is_dip(I, i) or (i > 0 and is_dip(I, i - 1)):
            continue
        pi = pidx.get(B[i]['d'])
        if pi is None or pi + 1 >= len(P):
            continue                                   # 다음 날 시가가 아직 없다
        end = min(pi + DIP_DAYS, len(P) - 1)
        if P[end]['d'] < since:
            continue
        tgt = P[pi + 1]['o'] * (1 + TARGET / 100)
        hit = next((j for j in range(pi + 1, end + 1) if P[j]['h'] >= tgt), None)
        out.append({'d0': B[i]['d'], 'buy': P[pi + 1]['d'], 'tgt': round(tgt, 2),
                    'hit': P[hit]['d'] if hit is not None else None,
                    'end': P[hit if hit is not None else end]['d'],
                    'open': hit is None and end - pi < DIP_DAYS})
    return out


def yellow(B, P=None, base=None, ud=False):
    """종목 카드의 옐로우닷. B: 판정에 쓰는 일봉. P가 있으면 B는 기초자산이고 P가 실제로 사는 상품이다
       (상황판의 SOXX→SOXL과 같은 방식). 종가가 고가·저가 밖으로 나간 봉은 범위를 종가까지 넓혀 계산한다.
       ud: 옐로우업다운으로 시작했다면의 과거 결과도 붙인다(etfs.json "ud")"""
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
    # 종목 상세의 작은 그래프: 최근 3개월 종가와 옐로우닷이 찍힌 날(기초자산으로 판정했으면 그 날짜를 상품 가격 위에 표시)
    dots = {B[i]['d'] for i in range(max(last - 70, 0), last + 1) if is_dip(I, i)}
    w = P[-63:]
    r['chart'] = {'from': w[0]['d'], 'to': w[-1]['d'], 'c': [round(x['c'], 2) for x in w],
                  'dot': [i for i, x in enumerate(w) if x['d'] in dots]}
    # 옐로우닷 다음 날 샀다면의 +10% 목표가(a~b 구간 점선)와 닿은 날(hit) — 그래프 칸 번호로
    widx = {x['d']: k for k, x in enumerate(w)}
    tg = []
    for t in dip_targets(B, I, P, w[0]['d']):
        a = widx.get(t['buy'], 0 if t['buy'] < w[0]['d'] else None)
        b = widx.get(t['end'])
        if a is not None and b is not None:
            tg.append({'a': a, 'b': b, 'y': t['tgt'], 'hit': widx.get(t['hit']) if t['hit'] else None})
    r['chart']['tg'] = tg
    if li is not None:
        d = B[li]['d']
        r['last'] = {'date': d, 'age': last - li, 'mv': round((B[-1]['c'] / B[li]['c'] - 1) * 100, 1)}
        pc = next((x['c'] for x in P if x['d'] == d), None) if P is not B else None
        if pc:
            r['last']['pmv'] = round((P[-1]['c'] / pc - 1) * 100, 1)
    if ud:
        r['ud'] = updown_past(B, I, P)
    return r


# ───────────── 옐로우업다운 (옐로우닷 시작) ─────────────
def ud_set():
    """옐로우업다운 과거 결과를 붙이는 종목 — etfs.json에서 "ud": true인 것"""
    try:
        with open('etfs.json', encoding='utf-8') as f:
            return {e['tk'] for e in json.load(f)['etfs'] if e.get('ud')}
    except (OSError, ValueError, KeyError):
        return set()


def updown_cycle(P, s):
    """P[s] 종가에 1포트 첫 매수(전날 종가 ×1.1 이하일 때). 원금 1.
       매일 장 마감 LOC: 매수 LP×(1−D−n×STEP), 매도 LP×(1+U−n×STEP), 매도 수량 = 보유 ÷ n. LP = 마지막 체결가(종가).
       금액 소진(포트를 다 쓰거나 남은 돈이 한 포트에 못 미침): LP를 매일 전날 종가로 바꿔 매도만.
       반환: 끝났거나 UD_MAXD가 지났으면 결과, 자료가 끝날 때까지 도는 중이면 'run'에 지금 상태. 시작 못 하면 None"""
    if s < 1 or s >= len(P) or P[s]['c'] > P[s - 1]['c'] * 1.1:
        return None
    N, D, U, st, cost = UD_POTS, UD_D, UD_U, UD_STEP, UD_COST
    pot = 1.0 / N
    cash = 1.0 - pot; sh = pot * (1 - cost) / P[s]['c']; n = 1; LP = P[s]['c']; low = 1.0; peak = 1
    end = min(s + UD_MAXD, len(P))
    for j in range(s + 1, end):
        c = P[j]['c']
        if n >= N or cash < pot * 0.999:
            LP = P[j - 1]['c']
            if c >= LP * (1 + U - n * st):
                q = sh / n; sh -= q; cash += q * c * (1 - cost); n -= 1; LP = c
        elif c <= LP * (1 - D - n * st):
            cash -= pot; sh += pot * (1 - cost) / c; n += 1; LP = c
        elif c >= LP * (1 + U - n * st):
            q = sh / n; sh -= q; cash += q * c * (1 - cost); n -= 1; LP = c
        peak = max(peak, n); low = min(low, cash + sh * c)
        if n == 0:
            return {'ret': (cash - 1) * 100, 'end': j, 'days': j - s, 'low': (low - 1) * 100, 'peak': peak, 'done': True}
    j = end - 1
    eq = cash + sh * P[j]['c'] * (1 - cost)
    if s + UD_MAXD > len(P):                           # 아직 도는 중 — 다음 거래일에 걸 LOC
        out = n >= N or cash < pot * 0.999
        base = P[j]['c'] if out else LP
        return {'run': {'since': P[s]['d'], 'n': n, 'out': out, 'ret': round((eq - 1) * 100, 1),
                        'buy': None if out else round(base * (1 - D - n * st), 2),
                        'sell': round(base * (1 + U - n * st), 2)}, 'end': j}
    return {'ret': (eq - 1) * 100, 'end': j, 'days': UD_MAXD, 'low': (low - 1) * 100, 'peak': peak, 'done': False}


def updown_past(B, I, P):
    """옐로우닷(B로 판정) 다음 거래일에 P로 옐로우업다운을 시작했다면 — 한 종목에 한 사이클씩(도는 중에 켜진 옐로우닷은 건너뜀).
       반환: 과거 결과 요약과 지금 상태(now: 도는 중이면 다음 LOC, 오늘 옐로우닷이 켜졌으면 내일 첫 매수 기준)"""
    pidx = {b['d']: i for i, b in enumerate(P)}
    rows = []; busy = -1; now = None
    for i in range(len(B)):
        if not is_dip(I, i) or (i > 0 and is_dip(I, i - 1)):
            continue
        pi = pidx.get(B[i]['d'])
        if pi is None or pi + 1 <= busy:
            continue
        if pi + 1 >= len(P):                           # 오늘 켜짐 — 첫 매수는 다음 거래일 장 마감 LOC
            now = {'pending': True, 'lim': round(P[pi]['c'] * 1.1, 2)}
            continue
        x = updown_cycle(P, pi + 1)
        if not x:
            continue
        busy = x['end']
        if 'run' in x:
            now = x['run']
        else:
            rows.append(x)
    if not rows:
        return {'n': 0, 'now': now} if now else None
    ret = [x['ret'] for x in rows]
    return {'n': len(rows), 'from': P[0]['d'], 'D': UD_D, 'U': UD_U, 'pots': UD_POTS,
            'mean': round(statistics.fmean(ret), 1), 'med': round(statistics.median(ret), 1),
            'win': round(sum(r > 0 for r in ret) / len(ret) * 100), 'worst': round(min(ret), 1),
            'days': round(statistics.median(x['days'] for x in rows)),
            'used': round(statistics.fmean(x['peak'] for x in rows), 1),
            'full': round(sum(x['peak'] >= UD_POTS for x in rows) / len(rows) * 100),
            'low': round(min(x['low'] for x in rows), 1),
            'undone': sum(not x['done'] for x in rows), 'now': now}


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
