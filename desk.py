#!/usr/bin/env python3
"""반등·과열 타이밍 상황판 — desk.json 생성 (Rule v2.0)

기준자산(SOXX·QQQ)의 확정 일봉으로 SCR 3단 신호를 판정하고, VIX 완화/악화,
주봉·월봉 상위 추세, UVIX 사이클을 합쳐 '현재 판단' 카드를 만든다.
어제 판정은 마지막 봉을 뺀 자료로 같은 계산을 한 번 더 돌려 구한다(미래 참조 금지).
"""
import urllib.request, urllib.parse, csv, io, json, time, datetime, sys

RULE = 'Rule v2.0'
UA = {'User-Agent': 'Mozilla/5.0'}
PAIRS = [('SOXL', 'SOXX'), ('TQQQ', 'QQQ')]   # (관찰 상품, 주 신호 계산 기준자산)
SYMS = ['SOXX', 'SOXL', 'QQQ', 'TQQQ', 'UVIX', 'VXX', 'SPY', '^VIX']
NEW_DAYS = 3      # 신규 신호 강조: 발생일 포함 3거래일 (D+0~D+2)
WINDOW = 10       # 준비 이후 순차 회복·이탈 허용 거래일


# ───────────── 자료 받기 ─────────────
def fetch(sym):
    """일봉 [{d,o,h,l,c}] (오래된 순). 실패하면 빈 리스트"""
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}'
           f'?period1=0&period2={int(time.time())}&interval=1d')
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.load(r)
            res = j['chart']['result'][0]
            q = res['indicators']['quote'][0]
            out = []
            for t, o, h, l, c in zip(res['timestamp'], q['open'], q['high'], q['low'], q['close']):
                if c is None:
                    continue
                d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat()
                out.append({'d': d, 'o': o, 'h': h if h is not None else c,
                            'l': l if l is not None else c, 'c': c})
            if out:
                return out
        except Exception as e:
            print(f'  yahoo 실패 {sym} ({attempt+1}/3): {e}')
            time.sleep(2)
    return []


def fetch_vx(date):
    """Cboe 일일 정산가에서 VIX 선물 근월·차근월 (만기가 date 뒤인 월물 순). 실패하면 None"""
    try:
        url = f'https://www.cboe.com/us/futures/market_statistics/settlement/csv?dt={date}'
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            txt = r.read().decode('utf-8', 'replace')
        seen = {}
        for row in csv.DictReader(io.StringIO(txt)):
            if row.get('Product') == 'VX' and (row.get('Symbol') or '').startswith('VX/'):
                exp = row['Expiration Date']
                if exp > date:
                    seen[exp] = float(row['Price'])
        exps = sorted(seen)
        if len(exps) >= 2 and seen[exps[0]] > 0 and seen[exps[1]] > 0:
            return {'vx1': seen[exps[0]], 'vx1exp': exps[0], 'vx2': seen[exps[1]], 'vx2exp': exps[1]}
    except Exception as e:
        print(f'  VIX 선물 실패: {e}')
    return None


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


# ───────────── SCR 3단 신호 ─────────────
def scr_signals(B, I):
    """과매도 준비→회복, 과매수 준비→이탈 신호를 시간순으로 찾는다.
       반환: (signals, 진행 중 준비 구간 {'up':…, 'dn':…})"""
    k, cc, rs = I['k'], I['cci'], I['rsi']
    sigs = []
    setup = {'up': None, 'dn': None}
    for i in range(len(B)):
        if k[i] is None or cc[i] is None or rs[i] is None:
            continue
        for side in ('up', 'dn'):
            if side == 'up':
                ready = k[i] <= 20 and cc[i] <= -100 and rs[i] <= 30
                clear = k[i] > 20 and cc[i] > -100 and rs[i] > 30
            else:
                ready = k[i] >= 80 and cc[i] >= 100 and rs[i] >= 70
                clear = k[i] < 80 and cc[i] < 100 and rs[i] < 70
            s = setup[side]
            if ready:
                if s is None:
                    s = setup[side] = {'start': i, 'last': i, 'ext': B[i]['c'], 'extI': i,
                                       'bb': False, 'disp': False}
                s['last'] = i
            if s is None:
                continue
            c = B[i]['c']
            if (side == 'up' and c < s['ext']) or (side == 'dn' and c > s['ext']):
                s['ext'] = c; s['extI'] = i
            if side == 'up':
                if I['bl'][i] is not None and B[i]['l'] <= I['bl'][i]: s['bb'] = True
                if I['disp'][i] is not None and I['disp'][i] <= 95: s['disp'] = True
            if ready:
                continue
            if i - s['last'] > WINDOW:
                setup[side] = None                       # 10거래일 안에 못 올라서면 만료
                continue
            if clear:
                sigs.append({'side': side, 'i': i, 'strict': s['last'] == i - 1,
                             'start': s['start'], 'ext': s['ext'], 'extI': s['extI'],
                             'bb': s['bb'], 'dispLow': s['disp'],
                             'hot': side == 'up' and k[i] >= 80})
                setup[side] = None                       # 같은 준비 구간에서 중복 신호 없음
    return sigs, setup


def divergence(B, I):
    """RSI 상승 다이버전스: RSI 피벗 저점(좌3·우3) 두 개가 5~60거래일 간격,
       가격 저가는 더 낮고 RSI는 더 높음. 가장 최근 확인된 것 하나"""
    rs = I['rsi']; piv = []
    for i in range(3, len(B) - 3):
        w = rs[i-3:i+4]
        if None in w:
            continue
        if all(rs[i] < x for j, x in enumerate(w) if j != 3):
            piv.append(i)
    for a, b in reversed(list(zip(piv, piv[1:]))):
        if 5 <= b - a <= 60 and B[b]['l'] < B[a]['l'] and rs[b] > rs[a]:
            return {'low': b, 'confirm': b + 3}
    return None


# ───────────── 주봉·월봉 ─────────────
def next_weekday(d):
    d = datetime.date.fromisoformat(d) + datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d


def period_bars(B, kind):
    """일봉을 주봉/월봉으로 묶는다. 반환: (bars, 마지막 봉 확정 여부)"""
    def key(ds):
        d = datetime.date.fromisoformat(ds) if isinstance(ds, str) else ds
        return d.isocalendar()[:2] if kind == 'w' else (d.year, d.month)
    out = []; cur = None
    for b in B:
        kk = key(b['d'])
        if cur is None or cur['key'] != kk:
            cur = {'key': kk, 'd': b['d'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']}
            out.append(cur)
        else:
            cur['h'] = max(cur['h'], b['h']); cur['l'] = min(cur['l'], b['l'])
            cur['c'] = b['c']; cur['d'] = b['d']
    done = key(next_weekday(B[-1]['d'])) != out[-1]['key']
    return out, done


def upper_trend(B, kind):
    P, done = period_bars(B, kind)
    conf = P if done else P[:-1]
    if len(conf) < 22:
        return None
    I = indicators(conf)
    sigs, _ = scr_signals(conf, I)
    brk = any(s['side'] == 'dn' and s['i'] >= len(conf) - 3 for s in sigs)   # 최근 확정 3봉 안의 3단 하향 이탈
    r = {'cci': round(I['cci'][-1], 1), 'rsi': round(I['rsi'][-1], 1),
         'weak': I['cci'][-1] < 0 or I['rsi'][-1] < 50, 'brk': brk, 'asof': conf[-1]['d'],
         'prev': None}
    if not done:
        Ip = indicators(P)
        r['prev'] = {'cci': round(Ip['cci'][-1], 1), 'rsi': round(Ip['rsi'][-1], 1)}
    return r


# ───────────── VIX ─────────────
def vix_state(V):
    """반환 {'st': worse|confirm|fast|neutral, 'why': …}. 악화가 완화보다 항상 우선"""
    if len(V) < 8:
        return None
    c = [b['c'] for b in V]; m5 = sma(c, 5)[-1]; k = stoch_k(V)
    chg = (c[-1] / c[-2] - 1) * 100
    r = {'c': round(c[-1], 2), 'chg': round(chg, 1), 'm5': round(m5, 2), 'above5': c[-1] >= m5,
         'k': round(k[-1], 1)}
    if chg >= 10:
        r.update(st='worse', why=f'VIX 하루 +{chg:.1f}% 급등')
    elif c[-1] > c[-2] > c[-3] and c[-1] >= m5:
        r.update(st='worse', why='VIX 2거래일 연속 상승 + 5일선 위')
    elif c[-1] < c[-2] < c[-3] and c[-1] < m5:
        r.update(st='confirm', why='VIX 2거래일 연속 하락 + 5일선 아래')
    else:
        why = []
        if c[-1] < c[-2]: why.append('전일 대비 하락')
        if V[-1]['o'] is not None and c[-1] < V[-1]['o']: why.append('당일 음봉')
        if k[-2] is not None and k[-2] >= 80 and k[-1] < 80: why.append('%K 80 하향 이탈')
        if why:
            r.update(st='fast', why='VIX ' + ' · '.join(why))
        else:
            r.update(st='neutral', why='VIX 완화 조짐 없음')
    return r

VIX_KO = {'worse': '악화', 'confirm': '완화 확인', 'fast': '빠른 완화 관찰', 'neutral': '중립·혼조'}


# ───────────── 종목 카드 ─────────────
def pct(a, b):
    return round((a / b - 1) * 100, 1)


def judge(base, prod, bars, n=None):
    """기준자산 base, 관찰 상품 prod의 '끝에서 n봉을 뺀 시점' 판정"""
    B = bars[base] if not n else bars[base][:-n]
    asof = B[-1]['d']
    P = [b for b in bars[prod] if b['d'] <= asof]
    V = [b for b in bars['^VIX'] if b['d'] <= asof]
    pidx = {b['d']: i for i, b in enumerate(P)}
    if len(B) < 60 or not P or P[-1]['d'] != asof or not V or V[-1]['d'] != asof:
        return {'asof': asof, 'state': 'wait', 'label': '⏳ 데이터 대기·판정 불가',
                'line': '최신 확정 거래일 데이터를 확인 중입니다.'}
    I = indicators(B)
    sigs, setup = scr_signals(B, I)
    vx = vix_state(V)
    wk = upper_trend(B, 'w'); mo = upper_trend(B, 'm')
    last = len(B) - 1; c = B[-1]['c']
    up = next((s for s in reversed(sigs) if s['side'] == 'up'), None)
    dn = next((s for s in reversed(sigs) if s['side'] == 'dn'), None)

    R = {'asof': asof, 'vix': vx['st'],
         'ind': {'k': round(I['k'][-1], 1), 'd': round(I['d'][-1], 1), 'cci': round(I['cci'][-1], 1),
                 'rsi': round(I['rsi'][-1], 1), 'disp': round(I['disp'][-1], 1),
                 'close': round(c, 2), 'pclose': round(P[-1]['c'], 2),
                 'chg': pct(c, B[-2]['c']), 'pchg': pct(P[-1]['c'], P[-2]['c'])},
         'ok': {'k': I['k'][-1] > 20, 'cci': I['cci'][-1] > -100, 'rsi': I['rsi'][-1] > 30},
         'wk': wk, 'mo': mo, 'sig': None, 'badges': []}

    # 최근 회복 신호의 신규성·유효성 (서로 독립)
    if up:
        age = last - up['i']
        broke = next((j for j in range(up['i'] + 1, len(B)) if B[j]['c'] < up['ext']), None)
        sg = {'kind': '엄격 동시 회복' if up['strict'] else '순차 회복', 'date': B[up['i']]['d'],
              'age': age, 'new': age < NEW_DAYS, 'valid': broke is None,
              'brokeDate': B[broke]['d'] if broke is not None else None,
              'low': round(up['ext'], 2), 'lowDate': B[up['extI']]['d'],
              'sigClose': round(B[up['i']]['c'], 2), 'hot': up['hot']}
        if up['i'] + 1 < len(B):
            sg['nextOpen'] = round(B[up['i'] + 1]['o'], 2)
            sg['mvOpen'] = pct(c, B[up['i'] + 1]['o'])
        sg['mvClose'] = pct(c, B[up['i']]['c'])
        sg['dist'] = round((c - up['ext']) / c * 100, 1)           # 기준자산 무효화 여유 거리
        pi = pidx.get(B[up['i']]['d']); pl = pidx.get(B[up['extI']]['d'])
        if pi is not None:
            sg['pSigClose'] = round(P[pi]['c'], 2); sg['pMvClose'] = pct(P[-1]['c'], P[pi]['c'])
            if pi + 1 < len(P):
                sg['pNextOpen'] = round(P[pi + 1]['o'], 2); sg['pMvOpen'] = pct(P[-1]['c'], P[pi + 1]['o'])
        if pl is not None:
            sg['pLow'] = round(P[pl]['c'], 2)
            sg['pDist'] = round((P[-1]['c'] - P[pl]['c']) / P[-1]['c'] * 100, 1)
        R['sig'] = sg
        if sg['valid']:
            if up['bb']: R['badges'].append('볼밴 하단 지지')
            if up['dispLow']: R['badges'].append('하방 이격 과대')
            dv = divergence(B, I)
            if dv and dv['low'] >= up['start'] - 5 and dv['confirm'] <= last:
                R['badges'].append('RSI 다이버전스 동반')
                R['div'] = {'low': B[dv['low']]['d'], 'confirm': B[dv['confirm']]['d']}
    dn_new = dn is not None and last - dn['i'] < NEW_DAYS
    up_live = up is not None and R['sig']['new'] and R['sig']['valid']
    if dn_new and up_live and dn['i'] > up['i']:
        up_live = False
    R['dnSig'] = {'date': B[dn['i']]['d'], 'age': last - dn['i'],
                  'kind': '엄격 동시 이탈' if dn['strict'] else '순차 이탈'} if dn else None

    serious = bool((wk and wk['brk']) or (mo and mo['brk']))
    weak = bool((wk and wk['weak']) or (mo and mo['weak']))
    R['weak'] = weak; R['serious'] = serious

    # 판정 우선순위: 데이터 → 위험 경고 → VIX 완화 수준 → 개별 신호
    if up_live:
        if vx['st'] == 'worse' or serious:
            st, lb = 'warn', '⚠️ 신규 진입 경계'
            ln = '단기 반등 신호가 있으나 시장 변동성 위험이 커 신규 진입은 경계가 필요합니다.'
            ln += ' (원인: ' + (vx['why'] if vx['st'] == 'worse' else '주봉·월봉 3단 하향 이탈') + ')'
        elif vx['st'] in ('confirm', 'fast'):
            st = 'buy'
            typ = '확인형' if vx['st'] == 'confirm' else '초기 반등형'
            R['type'] = typ
            lb = '🟢 매수 검토 우세 · ' + ('기술적 반등형(' + typ + ')' if weak else typ)
            ln = ('과매도에서 회복 중이며 변동성 완화도 이동평균선 아래에서 확인되었습니다. (분할 진입 검토 우세)'
                  if typ == '확인형' else
                  '과매도 회복과 함께 변동성 초기 진정 조짐이 나타났습니다. (신호 이후 가격 이동폭 확인 후 소액 분할 진입 검토)')
            if weak:
                ln = '일봉 SCR 회복 및 VIX 완화 확인 / 단, 주봉·월봉 상위 추세 약세 주의 (짧은 목표 수익 권장)'
        else:
            st, lb = 'watch', '🟡 매수 관찰'
            ln = '3단 회복은 끝났지만 VIX 완화가 아직 부족합니다. (관망·대기)'
    elif dn_new:
        st, lb = 'sell', '🔴 매도·회수 검토 우세'
        ln = '과열 이후 상승 모멘텀이 약해졌습니다. (보유자는 수익 회수 검토 / 미보유자는 진입 보류)'
    elif setup['up']:
        st, lb = 'watch', '🟡 매수 관찰'
        ln = '많이 내려왔지만 회복 조건이 아직 부족합니다. (미보유자 관망·대기 / 보유자 투매 자제)'
    elif setup['dn'] or I['disp'][-1] >= 105:
        st, lb = 'hot', '🟠 매도 관찰'
        ln = '과열 상태지만 상승 탄력은 유지되고 있습니다. (보유자는 수익 회수 준비 / 미보유자는 추격 매수 경계)'
    else:
        st, lb = 'neutral', '⚪ 중립·보유 관찰'
        ln = '뚜렷한 방향 신호 없이 중립 구간을 지나고 있습니다.'
        if up and R['sig']['valid'] and not (dn and dn['i'] > up['i']):
            ln += ' (기발생 유효 신호: 관찰 저점 관리)'
    R.update(state=st, label=lb, line=ln)
    R['setupUp'] = None
    if setup['up']:
        s = setup['up']
        R['setupUp'] = {'start': B[s['start']]['d'], 'left': WINDOW - (last - s['last']),
                        'low': round(s['ext'], 2)}
    R['setupDn'] = bool(setup['dn'])
    return R


def change_reason(y, t):
    """어제(y) 확정 판정과 오늘(t) 확정 판정을 비교한 한 줄"""
    if y['state'] == 'wait' or t['state'] == 'wait':
        return '어제 대비: 비교할 자료 부족'
    why = []
    ys, ts = y.get('sig'), t.get('sig')
    if ts and (not ys or ys['date'] != ts['date']):
        late = [nm for key, nm in (('k', '%K 20선'), ('cci', 'CCI -100선'), ('rsi', 'RSI 30선'))
                if not y['ok'][key]]
        why.append(f"{ts['kind']} 완료" + (f" — 마지막 미충족 조건 {' · '.join(late)} 회복" if late else ''))
    if ys and ts and ys['date'] == ts['date']:
        if ys['valid'] and not ts['valid']:
            why.append(f"기준자산 종가가 관찰 저점(${ts['low']}) 하회 → 신호 무효화")
        elif ys['new'] and not ts['new'] and ts['valid']:
            why.append('신규 3거래일 강조 종료 → 기발생 신호로 전환(유효 유지)')
    td, yd = t.get('dnSig'), y.get('dnSig')
    if td and td['age'] == 0:
        why.append(f"SCR 3단 하향 {td['kind']} 확정")
    elif td and yd and yd['age'] < NEW_DAYS <= td['age']:
        why.append('하향 이탈 신호 강조 3거래일 종료')
    if t.get('setupUp') and not y.get('setupUp'):
        why.append('3지표 동시 과매도 → 과매도 준비 진입')
    if y.get('setupUp') and not t.get('setupUp') and not (ts and (not ys or ys['date'] != ts['date'])):
        why.append('준비 후 10거래일 안에 회복 못 해 준비 만료')
    if t.get('setupDn') and not y.get('setupDn'):
        why.append('3지표 동시 과매수 → 과매수 준비 진입')
    if y['vix'] != t['vix']:
        why.append(f"VIX {VIX_KO[y['vix']]} → {VIX_KO[t['vix']]}")
    if y.get('weak') != t.get('weak'):
        why.append('상위 추세 약세 ' + ('발생' if t.get('weak') else '해소'))
    yl, tl = y['label'], t['label']
    if yl == tl:
        head = f"어제 대비: {tl.split(' ', 1)[1]} 유지"
        if ts and ts['new'] and ts['valid'] and t['state'] in ('buy', 'warn', 'watch'):
            head += f" ({ts['age'] + 1}거래일차)"
    else:
        head = f"어제 대비: {yl.split(' ', 1)[1]} → {tl.split(' ', 1)[1]}"
    return head + (' / ' + ' · '.join(why) if why else ' / 조건 변화 없음')


def risks(t, vxf):
    out = []
    for nm, u in (('주봉', t.get('wk')), ('월봉', t.get('mo'))):
        if u is None:
            out.append(f'{nm} 이력 부족')
        elif u['brk']:
            out.append(f'{nm} 3단 하향 이탈')
        elif u['weak']:
            out.append(f"{nm} 약세(CCI {u['cci']} · RSI {u['rsi']})")
    if not vxf:
        out.append('선물 확인 미완료 (현물 기준 부분 판정)')
    elif vxf['back']:
        out.append('VIX 선물 백워데이션(단기 공포 역전)')
    if t.get('sig') and t['sig']['new'] and t['sig']['hot']:
        out.append('회복 봉 %K 80 이상 — 단기 급등 과열 동반')
    return out or ['특이 위험 없음']


def next_states(base, t, vx):
    out = []
    sg = t.get('sig')
    if sg and sg['valid']:
        out.append(f"무효화: 기준자산({base}) 종가가 관찰 저점(${sg['low']})을 하회하면 '신호 무효화(저점 이탈)'")
    if t['state'] in ('buy', 'watch') and sg and sg['new'] and sg['valid']:
        if t.get('type') != '확인형':
            out.append(f"승격: VIX가 2거래일 연속 하락하며 5일선({vx['m5']}) 아래에 안착하면 '확인형'")
        out.append("경계: VIX가 하루 +10% 이상 급등하거나 2거래일 연속 상승하며 5일선을 넘으면 '⚠️ 신규 진입 경계'")
        if t.get('weak'):
            out.append("강화: 주봉 확정 CCI(20) 0선 · RSI(14) 50선을 회복하면 '기술적 반등형' 경고 해제")
    if t.get('setupUp'):
        out.append(f"회복: 남은 {t['setupUp']['left']}거래일 안에 %K 20 · CCI -100 · RSI 30을 모두 넘으면 회복 신호 "
                   f"(현재 %K {t['ind']['k']} · CCI {t['ind']['cci']} · RSI {t['ind']['rsi']})")
    if t['state'] == 'hot':
        out.append("이탈: %K 80 · CCI +100 · RSI 70 아래로 모두 내려오면 '🔴 매도·회수 검토 우세'")
    if t['state'] == 'neutral' and not t.get('setupUp'):
        out.append("관찰 시작: 같은 날 %K ≤ 20 · CCI ≤ -100 · RSI ≤ 30이면 '🟡 매수 관찰'")
        out.append("과열: 50일 이격도 105 이상이거나 3지표 동시 과매수면 '🟠 매도 관찰' "
                   f"(현재 이격도 {t['ind']['disp']})")
    return out


# ───────────── UVIX ─────────────
def uvix_card(U, X):
    c = [b['c'] for b in U]
    cyc = None; lastDone = None; lo = c[0]; doneToday = False
    for i in range(1, len(U)):
        doneToday = False
        if cyc is None:
            if c[i] < lo:
                cyc = {'start': i, 'low': c[i], 'lowI': i}
        else:
            if c[i] < cyc['low']:
                cyc['low'] = c[i]; cyc['lowI'] = i
            elif c[i] >= cyc['low'] * 1.10:
                lastDone = {'date': U[i]['d'], 'days': i - cyc['start'] + 1}
                cyc = None; doneToday = True
        lo = min(lo, c[i])
    last = len(U) - 1
    r = {'asof': U[-1]['d'], 'close': round(c[-1], 2), 'chg': pct(c[-1], c[-2]), 'lastDone': lastDone}
    if cyc:
        day = last - cyc['start'] + 1
        stage = ('초기 (1~3거래일)' if day <= 3 else '관찰 구간 (4~12거래일)' if day <= 12
                 else '연장 (13~25거래일)' if day <= 25 else '기간 초과 (25거래일 초과)')
        note = {'초': '신저가 갱신 직후입니다.', '관': '핵심 관찰 구간이지만 진입이 곧 반등 확정은 아닙니다.',
                '연': '저변동성 랠리 연장 — 무리한 비중 확대 경계.', '기': '사이클 장기화 경고.'}[stage[0]]
        hi = max(b['h'] for b in U[cyc['lowI']:])
        r['cycle'] = {'stage': stage, 'day': day, 'start': U[cyc['start']]['d'], 'low': round(cyc['low'], 2),
                      'lowDate': U[cyc['lowI']]['d'], 'fromLow': pct(c[-1], cyc['low']),
                      'target': round(cyc['low'] * 1.10, 2), 'hiHit': hi >= cyc['low'] * 1.10, 'note': note}
        xs = [b for b in X if b['d'] <= U[-1]['d']]
        if xs:
            xi = next((i for i, b in enumerate(xs) if b['d'] >= U[cyc['start']]['d']), len(xs))
            prior = min((b['c'] for b in xs[:xi]), default=None)
            r['vxxLow'] = prior is not None and min((b['c'] for b in xs[xi:]), default=prior) < prior
    else:
        r['cycle'] = {'stage': '완료' if doneToday else '대기', 'note':
                      '사이클 최저 종가 대비 +10% 회복으로 사이클이 끝났습니다.' if doneToday else
                      '직전 반등 완료 후 아직 사상 신저가(종가)를 갱신하지 않았습니다.',
                      'allLow': round(lo, 2), 'toLow': round((c[-1] - lo) / c[-1] * 100, 1)}
    # 월중 급등·피크아웃
    ym = U[-1]['d'][:7]
    mi = [i for i, b in enumerate(U) if b['d'][:7] == ym]
    ml = min(mi, key=lambda i: c[i])
    I = indicators(U)
    sigs, _ = scr_signals(U, I)
    peak = max(c[ml:]) / c[ml] - 1
    surged = peak >= 0.25
    pk = surged and any(s['side'] == 'dn' and s['i'] > ml for s in sigs)
    ob = I['k'][-1] >= 80 and I['cci'][-1] >= 100 and I['rsi'][-1] >= 70
    rise = pct(c[-1], c[ml])
    r['month'] = {'lowClose': round(c[ml], 2), 'lowDate': U[ml]['d'],
                  'lowIntra': round(min(U[i]['l'] for i in mi), 2), 'rise': rise,
                  'peakRise': round(peak * 100, 1), 'ob': ob, 'peakout': pk,
                  'k': round(I['k'][-1], 1), 'cci': round(I['cci'][-1], 1), 'rsi': round(I['rsi'][-1], 1),
                  'status': ('급등 후 SCR 꺾임 확인 (피크아웃 관찰)' if pk else
                             '과열 (+30% 이상)' if rise >= 30 else '과열 진입 (+25% 이상)' if rise >= 25 else
                             '급등 후 되돌림 (SCR 꺾임 신호는 없음)' if surged else '급등 아님')}
    return r


# ───────────── 시장 환경 ─────────────
def market_card(S, V, vx, vxf):
    sc = pct(S[-1]['c'], S[-2]['c'])
    r = {'spy': round(S[-1]['c'], 2), 'spyChg': sc, 'vix': vx, 'vixLabel': VIX_KO[vx['st']], 'fut': vxf}
    vc = vx['chg']
    r['couple'] = ('고점 경계 관찰' if sc > 0 and vc > 0 else '바닥 소진 관찰' if sc < 0 and vc < 0 else None)
    return r


def history(base, prod, bars, today, n=21):
    """최근 n거래일의 봉·지표·당일 기준 판정(그날까지의 자료만으로 계산)·마커"""
    js = [today] + [judge(base, prod, bars, k) for k in range(1, n + 1)]
    B = bars[base]; I = indicators(B); dv = divergence(B, I)
    if dv and dv['confirm'] > len(B) - 1:
        dv = None
    r2 = lambda v: None if v is None else round(v, 2)
    out = []
    for k in range(n - 1, -1, -1):
        t, y = js[k], js[k + 1]; i = len(B) - 1 - k; b = B[i]
        if t['state'] == 'wait':
            continue
        kk, cc, rs = I['k'][i], I['cci'][i], I['rsi'][i]
        marks = []
        if kk <= 20 and cc <= -100 and rs <= 30: marks.append('ro')
        if kk >= 80 and cc >= 100 and rs >= 70: marks.append('rb')
        if t['sig'] and t['sig']['date'] == b['d']: marks.append('us' if t['sig']['kind'][0] == '엄' else 'uq')
        if t['dnSig'] and t['dnSig']['date'] == b['d']: marks.append('dn')
        if t['sig'] and t['sig']['brokeDate'] == b['d']: marks.append('x')
        if dv and dv['low'] == i: marks.append('dl')
        if dv and dv['confirm'] == i: marks.append('dc')
        out.append({'d': b['d'], 'o': r2(b['o']), 'h': r2(b['h']), 'l': r2(b['l']), 'c': r2(b['c']),
                    'bu': r2(I['bu'][i]), 'bl': r2(I['bl'][i]), 'm50': r2(I['m50'][i]),
                    'k': round(kk, 1), 'cci': round(cc, 1), 'rsi': round(rs, 1), 'disp': round(I['disp'][i], 1),
                    'state': t['state'], 'label': t['label'], 'change': change_reason(y, t), 'marks': marks})
    return out


def main():
    bars = {}
    for s in SYMS:
        bars[s] = fetch(s)
        print(f'{s}: {len(bars[s])}봉' + (f" ~ {bars[s][-1]['d']}" if bars[s] else ''))
        time.sleep(0.4)
    need = ['SOXX', 'QQQ', '^VIX', 'SPY']
    if any(not bars[s] for s in need):
        print('핵심 자료가 비어 저장하지 않고 중단'); sys.exit(1)
    # 기준 거래일: 미국 주식 마지막 확정 봉. VIX가 더 앞서 있으면 맞춘다
    asof = bars['SPY'][-1]['d']
    for s in SYMS:
        bars[s] = [b for b in bars[s] if b['d'] <= asof]

    vx = vix_state(bars['^VIX'])
    fut = fetch_vx(bars['^VIX'][-1]['d'])
    vxf = None
    if fut:
        spot = bars['^VIX'][-1]['c']
        vxf = {**fut, 'sp1': round(fut['vx1'] - spot, 2), 'sp2': round(fut['vx2'] - fut['vx1'], 2),
               'back': fut['vx1'] < spot or fut['vx2'] < fut['vx1'],
               'shape': ('백워데이션(단기 공포 역전)' if fut['vx1'] < spot or fut['vx2'] < fut['vx1']
                         else '콘탱고(정상)')}

    cards = []
    for prod, base in PAIRS:
        if not bars[prod]:
            cards.append({'prod': prod, 'base': base, 'state': 'wait', 'label': '⏳ 데이터 대기·판정 불가',
                          'line': '최신 확정 거래일 데이터를 확인 중입니다.'})
            continue
        t = judge(base, prod, bars)
        y = judge(base, prod, bars, 1)
        t.update(prod=prod, base=base)
        if t['state'] != 'wait':
            t['change'] = change_reason(y, t)
            t['risks'] = risks(t, vxf)
            t['next'] = next_states(base, t, vx)
            t['hist'] = history(base, prod, bars, t)
        cards.append(t)
        print(f"{prod}({base}): {t['label']} | {t.get('change', '')}")

    out = {'rule': RULE, 'asof': asof,
           'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='minutes'),
           'cards': cards,
           'uvix': uvix_card(bars['UVIX'], bars['VXX']) if len(bars['UVIX']) > 60 else None,
           'market': market_card(bars['SPY'], bars['^VIX'], vx, vxf)}
    with open('desk.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('desk.json 저장 —', asof)


if __name__ == '__main__':
    main()
