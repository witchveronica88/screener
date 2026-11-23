#!/usr/bin/env python3
"""옐로우닷 상황판 — desk.json 생성 (옐로우닷 v3.0)

v3.0 (2026-11-23 사용자: 블로그 원문은 이제 중요하지 않다 — 원문 내용은 지우고 '옐로우닷'과 '옐로우업다운'으로 우리 방법을 찾자)
  남긴 것은 우리 방법 둘뿐이다.
  - 옐로우닷: 기준자산(SPY·QQQ·SOXX)의 세 지표가 모두 바닥권인 날. 바닥권 = 빠르기 %K ≤ 20 · CCI ≤ −100 · RSI ≤ 30
    (짧은 사이에 너무 많이 빠졌다는 뜻 · 예전 이름 '노란 점'). 계산은 종가 수집과 같은 market.is_dip.
  - 옐로우업다운: 옐로우닷 다음 거래일 장 마감에 1포트로 시작하는 예비 자금 운용(market.updown_past) — 종목 탭에서만 보인다.
    상황판은 2026-11-23 사용자 말대로 옐로우업다운을 쓰지 않는다('그날의 상황만 알고 싶다, 옐로우업다운 매수·매도는 빼고 옐로우닷이 떴다 정도만').
    과거 +10%·+20% 도달률은 SPY→UPRO · QQQ→TQQQ · SOXX→SOXL 3배 상품으로 센다(2026-11-23 사용자: SPY 3배는 주가가 낮은 UPRO로 — 그전에는 SPXL,
    도달률은 SPY 1배로 세어 세 줄의 숫자를 견줄 수 없었다).
  뺀 것: 원문(블로그) 🟢🟡 판정, 반등 신호·취소선, VIX 완화 단계, 주·월 단위 흐름, UVIX 새 최저가 사이클,
         반등 신호 성적표·기록, 지난 판정 기록.
  근거(2026-11-23 백테스트 — 9종목 · 옐로우업다운 −5%·+10% · 비용 0.1% · 3종목까지 동시에 · 2011~2026, 연 수익/최대 낙폭):
    옐로우닷 그대로 +9.7%/−29% (2011~18 +5.2% · 2019~26 +12.2%)
    원문 'VIX 악화인 날 거르기' +6.5%/−19% — 두 기간 모두 수익이 줄었다(사이클 127 → 50번)
    원문 '반등 신호까지 기다리기' +10.6%/−37% — 앞 기간만 나았고(+7.1%) 뒤 기간은 못했으며(+9.8%) 낙폭이 컸다
    200일 평균 위에서만 +3.8%/−22% · 아래에서만 +9.2%/−37% — 나아지지 않았다
    RSI 35로 넓히기 +13.5%/−37% — 더 자주 사서 수익과 낙폭이 함께 커졌다. 사용자가 정하기 전까지 30 그대로
  VIX·UVIX는 판정에 쓰지 않고 참고로만 보여 준다.
  desk_log.json은 v2.x 원문 판정을 화면에 보인 그대로 남긴 기록(2026-11-23까지)이다. 더 쓰지 않고 고치지도 않는다.
"""
import urllib.request, csv, io, json, time, datetime, sys
from market import (UA, fetch, sma, indicators, is_dip, dip_past, dip_targets, TARGET, TARGET2, DIP_DAYS, DIP_FAST)

RULE = '옐로우닷 v3.0'
PAIRS = [('UPRO', 'SPY'), ('TQQQ', 'QQQ'), ('SOXL', 'SOXX')]   # (과거 결과를 셀 3배 상품, 기준자산) — 화면 순서
SYMS = ['SPY', 'QQQ', 'SOXX', 'TQQQ', 'SOXL', 'UPRO', 'UVIX', '^VIX']
NAMES = {'SPY': 'S&P500', 'QQQ': '나스닥', 'SOXX': '반도체'}   # 결론 문장에 쓰는 우리말 이름
RECENT = 20       # 옐로우닷이 꺼진 지 이 거래일 안이면 결론 문장에 마지막 옐로우닷을 적는다


# ───────────── 자료 받기 ─────────────
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


def pct(a, b):
    return round((a / b - 1) * 100, 1)


def md(d):
    return f"{int(d[5:7])}/{int(d[8:10])}"


def bad_data(B):
    """분할·병합 직후 보정이 덜 된 자료 — 하루 25% 넘게 달라졌거나 종가가 고가·저가 밖"""
    return abs(B[-1]['c'] / B[-2]['c'] - 1) > 0.25 or any(not (b['l'] <= b['c'] <= b['h']) for b in B[-5:])


# ───────────── 옐로우닷 · 오늘의 해석 ─────────────
def dip_card(base, prod, bars, I, vix):
    """옐로우닷 상태 · 과거 결과 · '오늘의 해석'(쉬운 말 한 문장과 불렛) — 그날의 상황만, 매수·매도 안내는 쓰지 않는다"""
    B = bars[base]; last = len(B) - 1
    P = [b for b in bars[prod] if b['d'] <= B[-1]['d']]
    S = {b['d']: b['c'] for b in bars['SPY']}
    pc = {b['d']: b['c'] for b in P}
    on = is_dip(I, last)
    run = 0
    while on and run <= last and is_dip(I, last - run):
        run += 1
    li = next((i for i in range(last, max(last - 250, 0), -1) if is_dip(I, i)), None)
    r = {'on': on, 'run': run, 'past': dip_past(B, I, P),
         'k': round(I['k'][-1], 1), 'cci': round(I['cci'][-1], 1), 'rsi': round(I['rsi'][-1], 1)}
    if li is not None:
        d = B[li]['d']
        r['last'] = {'date': d, 'age': last - li, 'close': round(B[li]['c'], 2), 'mv': pct(B[-1]['c'], B[li]['c']),
                     'pmv': pct(P[-1]['c'], pc[d]) if d in pc and P else None}
    # 최근 한 달 고점에서 얼마나, 며칠 만에 빠졌나. 같은 기간 시장 전체(SPY)는 어땠나
    w = B[-21:]
    hi = max(range(len(w)), key=lambda i: w[i]['c'])
    lo = min(range(hi, len(w)), key=lambda i: w[i]['c'])
    drop = pct(w[lo]['c'], w[hi]['c']); days = lo - hi
    spy = (pct(S[w[lo]['d']], S[w[hi]['d']]) if w[lo]['d'] in S and w[hi]['d'] in S and base != 'SPY' else None)
    back = pct(B[-1]['c'], w[lo]['c']) if lo < len(w) - 1 else None
    r['drop'] = {'pct': drop, 'days': days, 'from': w[hi]['d'], 'to': w[lo]['d'], 'spy': spy, 'back': back}
    if days and drop <= -3 and spy is not None:
        r['drop']['note'] = '시장 전체가 함께 빠진 하락입니다' if spy <= drop / 3 else '시장보다 이 종목군이 유독 많이 빠졌습니다'

    lastd = r.get('last')
    ok = lambda v: ' ✓' if v else ''
    cond = (f"옐로우닷 조건: 빠르기 {r['k']} (20 이하{ok(r['k'] <= 20)}) · 벗어난 정도 {r['cci']} (−100 이하{ok(r['cci'] <= -100)})"
            f" · 힘 {r['rsi']} (30 이하{ok(r['rsi'] <= 30)})")
    vl = f"참고: 시장 불안(VIX) {vix['c']} (전날보다 {vix['chg']:+.1f}%) — 판정에는 쓰지 않음" if vix else None
    if on:
        # 켜진 날수는 결론 문장에만 — 불렛에서 다시 말하지 않는다
        head = (f'오늘 옐로우닷이 켜졌습니다. {base}가 급하게 내려 세 지표가 모두 바닥권에 들어갔습니다.' if run <= 1 else
                f'옐로우닷이 {run}일째 켜져 있습니다. {base}의 세 지표가 모두 바닥권에 머물러 있습니다.')
        why = ['바닥권: 빠르기·벗어난 정도·힘 세 지표가 모두 기준선 아래 — 짧은 사이에 너무 많이 빠졌다는 뜻입니다',
               '바닥이 확인된 것은 아닙니다. 내려오는 중이라 더 빠질 수도 있습니다', vl]
    elif lastd and lastd['age'] <= RECENT:
        head = f"옐로우닷은 {lastd['age']}거래일 전({md(lastd['date'])})에 꺼졌습니다. 그 뒤 {base} {lastd['mv']:+.1f}%입니다."
        why = [cond, vl]
    else:
        head = '옐로우닷이 꺼져 있습니다. 세 지표가 모두 바닥권에 들어가야 켜집니다.'
        why = [cond, f"마지막 옐로우닷은 {md(lastd['date'])} ({lastd['age']}거래일 전), 그 뒤 {base} {lastd['mv']:+.1f}%"
               if lastd else None, vl]
    r['head'] = head; r['why'] = [x for x in why if x]
    return r


def history(base, prod, bars, I, n=21):
    """최근 n거래일의 봉·지표·옐로우닷·+10% 닿은 날(옐로우닷 다음 날 상품을 샀다면)"""
    B = bars[base]; P = bars.get(prod) or B
    hits = {t['hit'] for t in dip_targets(B, I, P, B[max(0, len(B) - n)]['d']) if t['hit']}
    r2 = lambda v: None if v is None else round(v, 2)
    out = []
    for i in range(max(0, len(B) - n), len(B)):
        b = B[i]; dot = is_dip(I, i)
        out.append({'d': b['d'], 'o': r2(b['o']), 'h': r2(b['h']), 'l': r2(b['l']), 'c': r2(b['c']),
                    'bu': r2(I['bu'][i]), 'bl': r2(I['bl'][i]), 'm50': r2(I['m50'][i]),
                    'k': round(I['k'][i], 1), 'cci': round(I['cci'][i], 1), 'rsi': round(I['rsi'][i], 1),
                    'disp': round(I['disp'][i], 1), 'dot': dot,
                    'marks': (['ro'] if dot else []) + (['tg'] if b['d'] in hits else [])})
    return out


def stage_of(c):
    """요약 줄 상태 — 옐로우닷(켜짐·꺼짐) · 짧은 숫자(stats)"""
    if c['state'] == 'wait':
        return {'on': False, 'cls': 'wait', 'text': '자료 확인 중', 'stats': []}
    d = c['dip']; last = d.get('last')
    if d['on']:
        text, cls = (f"옐로우닷 {d['run']}일째" if d['run'] > 1 else '옐로우닷 오늘'), 'on'
    else:
        text, cls = '옐로우닷 꺼짐', 'off'                # 마지막 날짜는 stats('옐로우닷(10/14) 뒤')가 말한다
    stats = []
    past = d.get('past')
    if d['on'] and past:                               # 켜진 날: 과거에 다음 날 샀다면 +10%에 15·30거래일 안(DIP_FAST·DIP_DAYS) — 화면이 색 칸으로
        stats.append(('과거', [past.get('hitF'), past['hit']], 'past'))
    elif last and last.get('pmv') is not None:
        stats.append((f"옐로우닷({md(last['date'])}) 뒤", f"{last['pmv']:+.1f}%", 'dot'))
    return {'on': d['on'], 'cls': cls, 'text': text, 'stats': stats}


def headline(cards):
    """요약 첫 줄 — 옐로우닷이 켜진 곳 (그날의 상황만 · 매수·매도 안내는 쓰지 않는다)"""
    live = [c for c in cards if c['state'] != 'wait']
    if not live:
        return '자료를 확인하는 중입니다.'
    nm = lambda cs: '·'.join(NAMES.get(c['base'], c['base']) for c in cs) + (' 모두' if len(cs) == len(live) > 1 else '')
    on = [c for c in live if c['dip']['on']]
    out = []
    if on:                                             # 켜진 날수·꺼진 곳은 아래 세 줄이 보여 준다 — 여기서 다시 말하지 않는다
        out.append(f'{nm(on)} 옐로우닷이 켜졌습니다.')
    else:
        rec = sorted((c for c in live if c['dip'].get('last') and c['dip']['last']['age'] <= RECENT),
                     key=lambda c: c['dip']['last']['age'])
        out.append('옐로우닷이 모두 꺼져 있습니다.' + (f" 마지막은 {rec[0]['base']} {md(rec[0]['dip']['last']['date'])}입니다." if rec else ''))
    return ' '.join(out)


def live_state(B, n=14):
    """장중 '지금 가격으로 보기'용 — 마지막 확정 종가까지의 지표 상태(2026-11-23 사용자: 장중에 급변하는 날 버튼 하나로 지금 상황을).
       화면이 오늘 봉(지금까지 고가·저가·현재가)만 더해 %K(5)·CCI(20)·RSI(14)를 market.indicators와 같은 셈으로 다시 센다"""
    c = [b['c'] for b in B]
    d = [c[i] - c[i-1] for i in range(1, len(c))]
    g = sum(x for x in d[:n] if x > 0) / n; l = sum(-x for x in d[:n] if x < 0) / n
    for i in range(n, len(d)):
        g = (g * (n-1) + max(d[i], 0)) / n; l = (l * (n-1) + max(-d[i], 0)) / n
    r6 = lambda v: round(v, 6)
    return {'d': B[-1]['d'], 'pc': r6(c[-1]), 'g': r6(g), 'l': r6(l),
            'h4': [r6(b['h']) for b in B[-4:]], 'l4': [r6(b['l']) for b in B[-4:]],
            'tp19': [r6((b['h'] + b['l'] + b['c']) / 3) for b in B[-19:]]}


def card(prod, base, bars, vix):
    B = bars[base]; asof = B[-1]['d']
    P = [b for b in bars.get(prod) or [] if b['d'] <= asof]
    c0 = {'prod': prod, 'base': base}
    if len(B) < 60 or not P or P[-1]['d'] != asof:
        return {**c0, 'state': 'wait', 'line': '최신 확정 거래일 데이터를 확인 중입니다.'}
    if bad_data(B):
        return {**c0, 'state': 'wait', 'line': '기준자산 가격이 하루 만에 크게 달라졌습니다. 분할·병합 보정이 끝났는지 확인 중입니다.'}
    I = indicators(B); c = B[-1]['c']
    r = {**c0, 'state': 'ok', 'asof': asof,
         'ind': {'k': round(I['k'][-1], 1), 'd': round(I['d'][-1], 1), 'cci': round(I['cci'][-1], 1),
                 'rsi': round(I['rsi'][-1], 1), 'disp': round(I['disp'][-1], 1),
                 'close': round(c, 2), 'pclose': round(P[-1]['c'], 2),
                 'chg': pct(c, B[-2]['c']), 'pchg': pct(P[-1]['c'], P[-2]['c'])}}
    r['dip'] = dip_card(base, prod, bars, I, vix)
    r['hist'] = history(base, prod, bars, I)
    r['stage'] = stage_of(r)
    r['live'] = live_state(B)
    return r


# ───────────── 시장 참고: VIX · UVIX (판정에 쓰지 않음) ─────────────
def vix_brief(V):
    c = [b['c'] for b in V]; m5 = sma(c, 5)
    return {'c': round(c[-1], 2), 'chg': pct(c[-1], c[-2]), 'm5': round(m5[-1], 2), 'above5': c[-1] >= m5[-1],
            'hi1m': c[-1] >= max(c[-21:]), 'lo1m': c[-1] <= min(c[-21:])}


def market_card(S, V, vxf):
    m5 = sma([b['c'] for b in V], 5)
    return {'spy': round(S[-1]['c'], 2), 'spyChg': pct(S[-1]['c'], S[-2]['c']), 'vix': vix_brief(V), 'fut': vxf,
            'spark': [{'d': V[i]['d'], 'c': round(V[i]['c'], 2), 'm5': round(m5[i], 2)} for i in range(len(V) - 21, len(V))]}


def uvix_brief(U):
    c = [b['c'] for b in U]
    return {'close': round(c[-1], 2), 'chg': pct(c[-1], c[-2]), 'jump': not 0.4 < c[-1] / c[-2] < 3,   # 역병합 보정이 덜 됐을 수 있는 급변
            'spark': [{'d': b['d'], 'c': round(b['c'], 2)} for b in U[-21:]], 'allLow': round(min(c), 2)}


UVIX_DAYS = 60    # UVIX 옐로우닷 뒤 SPY·QQQ·SOXX를 견주는 기간 — 옐로우닷 과거 결과 기간(15·30)과 다른 숫자라 따로 둔다


def uvix_dip(U, bars, days=UVIX_DAYS):
    """UVIX 옐로우닷 — 참고용(사용자 요청, 2026-11). UVIX의 세 지표가 모두 바닥권인 날 = 불안이 급하게 가라앉아
       시장이 아주 조용해진 날. 주식 옐로우닷과 뜻이 반대이고 UVIX는 오래 들고 있으면 녹아내리므로 판정에는 쓰지 않는다.
       ① UVIX를 다음 날 샀다면 옐로우닷 과거 결과와 같은 기간(DIP_DAYS) 안 +10%·+20% — 아무 날 샀을 때와 함께
       ② 그 뒤 60거래일 SPY·QQQ·SOXX(종가 기준 평균 등락 · 오른 비율 · -10% 넘게 빠진 비율) — 같은 기간 아무 날과 함께"""
    if len(U) < 60:
        return None
    I = indicators(U); last = len(U) - 1
    on = is_dip(I, last); run = 0
    while on and run <= last and is_dip(I, last - run):
        run += 1
    li = next((i for i in range(last, 0, -1) if is_dip(I, i)), None)
    starts = [i for i in range(1, len(U)) if is_dip(I, i) and not is_dip(I, i - 1)]
    h1 = h2 = n = 0
    for i in range(len(U) - DIP_DAYS - 1):
        e = U[i + 1]['o']; hi = max(U[j]['h'] for j in range(i + 1, i + DIP_DAYS + 1))
        n += 1; h1 += hi >= e * (1 + TARGET / 100); h2 += hi >= e * (1 + TARGET2 / 100)
    out = {'on': on, 'run': run, 'past': dip_past(U, I, U), 'from': U[0]['d'],
           'any': {'hit': round(h1 / n * 100), 'hit20': round(h2 / n * 100), 'n': n} if n else None,
           'drop': round((U[-1]['c'] / max(b['c'] for b in U) - 1) * 100, 2), 'stocks': []}   # -99.95를 -100으로 반올림하지 않게
    if li is not None:
        out['last'] = {'date': U[li]['d'], 'age': last - li}

    def fwd(P, i):
        c0 = P[i]['c']
        return P[i + days]['c'] / c0 - 1, min(P[j]['l'] for j in range(i + 1, i + days + 1)) / c0 - 1

    def stat(rows):
        return {'n': len(rows), 'ret': round(sum(r for r, _ in rows) / len(rows) * 100, 1),
                'up': round(sum(1 for r, _ in rows if r > 0) / len(rows) * 100),
                'dd10': round(sum(1 for _, d in rows if d <= -0.10) / len(rows) * 100)} if rows else None

    sd = {U[i]['d'] for i in starts}
    for s in ('SPY', 'QQQ', 'SOXX'):
        P = bars.get(s) or []
        ok = [i for i, b in enumerate(P) if b['d'] >= U[0]['d'] and i + days < len(P)]
        a, b_ = stat([fwd(P, i) for i in ok if P[i]['d'] in sd]), stat([fwd(P, i) for i in ok])
        if a and b_:
            out['stocks'].append({'s': s, 'dip': a, 'all': b_})
    return out


def summary_row(sym, B):
    """지표 값 칸 한 종목 — 세 지표 · 50일 평균 대비 · 평소 범위 안 위치 · 1년 고점 대비"""
    if len(B) < 60:
        return {'sym': sym, 'na': True}
    I = indicators(B); c = B[-1]['c']
    pos = (c - I['bl'][-1]) / (I['bu'][-1] - I['bl'][-1]) * 100 if I['bu'][-1] != I['bl'][-1] else 50
    bb = ('위쪽 끝 밖' if pos > 100 else '위쪽 끝 근처' if pos >= 80 else '아래쪽 끝 밖' if pos < 0 else
          '아래쪽 끝 근처' if pos <= 20 else '가운데')
    return {'sym': sym, 'close': round(c, 2), 'chg': pct(c, B[-2]['c']), 'k': round(I['k'][-1], 1),
            'd': round(I['d'][-1], 1), 'cci': round(I['cci'][-1], 1), 'rsi': round(I['rsi'][-1], 1),
            'disp': round(I['disp'][-1], 1), 'bb': f'{bb} ({pos:.0f}%)',
            'hi1y': pct(c, max(b['c'] for b in B[-252:]))}


def main():
    bars = {}
    for s in SYMS:
        bars[s] = fetch(s)          # 장이 아직 안 끝난 오늘 봉은 빠져 있다
        print(f'{s}: {len(bars[s])}봉' + (f" ~ {bars[s][-1]['d']}" if bars[s] else ''))
        time.sleep(0.4)
    if any(not bars[s] for s in ('SPY', 'QQQ', 'SOXX', '^VIX')):
        print('핵심 자료가 비어 저장하지 않고 중단'); sys.exit(1)
    asof = bars['SPY'][-1]['d']                       # 기준 거래일: 미국 주식 마지막 확정 봉. VIX가 더 앞서 있으면 맞춘다
    for s in SYMS:
        bars[s] = [b for b in bars[s] if b['d'] <= asof]

    fut = fetch_vx(bars['^VIX'][-1]['d'])
    vxf = None
    if fut:
        spot = bars['^VIX'][-1]['c']
        vxf = {**fut, 'back': fut['vx1'] < spot or fut['vx2'] < fut['vx1']}
    mk = market_card(bars['SPY'], bars['^VIX'], vxf)
    cards = []
    for prod, base in PAIRS:
        c = card(prod, base, bars, mk['vix'])
        cards.append(c)
        print(f"{base}({prod}): {(c.get('stage') or {}).get('text', c.get('line'))}")
    out = {'rule': RULE, 'asof': asof, 'target': TARGET,
           'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='minutes'),
           'cards': cards, 'market': mk, 'headline': headline(cards),
           'uvix': uvix_brief(bars['UVIX']) if len(bars['UVIX']) > 60 else None,
           'uvixDip': uvix_dip(bars['UVIX'], bars),
           'table': [summary_row(s.replace('^', ''), bars[s]) for s in SYMS]}
    with open('desk.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('desk.json 저장 —', asof, '|', out['headline'])


if __name__ == '__main__':
    main()
