#!/usr/bin/env python3
"""종가 수집 — data.json 생성 (종목 탭)

종목 목록은 etfs.json(레버리지 ETF) + tickers.txt(직접 추가한 종목)이다.
가격 받기·지표·노란 점 계산은 market.py에 있고, 상황판(desk.py)도 같은 것을 쓴다.
desk.py를 불러오지 않으므로 상황판 코드가 깨져도 종가는 저장된다.
"""
import json, math, time, datetime, os, sys
import market

ETF = json.load(open('etfs.json', encoding='utf-8'))
BASE = [e['tk'] for e in ETF['etfs']]

# tickers.txt — 개별주식 등 직접 추가한 티커 (한 줄에 하나, # 뒤는 메모)
EXTRA = []
if os.path.exists('tickers.txt'):
    for line in open('tickers.txt', encoding='utf-8'):
        t = line.split('#')[0].strip().upper()
        if t and t not in BASE and t not in EXTRA:
            EXTRA.append(t)
TICKERS = BASE + EXTRA

# 레버리지 상품 → (기초자산, 일일 배수). 기초자산은 기초지수와 완전히 같지 않을 수 있는 근사치다.
# etfs.json의 base·mult와, 종목 탭에는 개별주로 보이지만 기초자산이 있는 상품(extra)
REF = {e['tk']: (e['base'], e['mult']) for e in ETF['etfs'] if e.get('base')}
REF.update({t: (x['base'], x['mult']) for t, x in ETF.get('extra', {}).items()})

RISK_N = 750   # 급락 위험을 재는 공통 구간: 최근 3년(750거래일)


def maxdd(closes):
    """보유 이력 전체에서 고점 대비 최대 하락폭 (%)"""
    peak = closes[0]; worst = 0.0
    for c in closes:
        if c > peak: peak = c
        d = c / peak - 1
        if d < worst: worst = d
    return worst * 100


def sigma(closes, n=250):
    """최근 n거래일 일간 변동성 (%). 250일=구조적, 60일=최근 국면
       반환: (변동성, 사용한 수익률 개수)"""
    c = closes[-(n+1):]
    if len(c) < 21:
        return None, 0
    r = [math.log(c[i]/c[i-1]) for i in range(1, len(c)) if c[i-1] > 0]
    m = sum(r) / len(r)
    v = sum((x-m)**2 for x in r) / (len(r)-1)
    return math.sqrt(v) * 100, len(r)


def corr(a, b, days):
    """기준 거래일 구간에서 두 종목 모두 종가가 있는 날의 일간 로그수익률 상관"""
    xs, ys = [], []
    for d0, d1 in zip(days, days[1:]):
        if d0 in a and d1 in a and d0 in b and d1 in b and a[d0] > 0 and b[d0] > 0:
            xs.append(math.log(a[d1] / a[d0]))
            ys.append(math.log(b[d1] / b[d0]))
    n = len(xs)
    if n < 60:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def tail_loss(closes, k):
    """최근 3년 k일 수익률의 하위 5% 평균 (%). 반환: (평균, 꼬리 표본 수) — 표본 5개 미만이면 None"""
    c = closes[-(RISK_N + 1):]
    r = sorted(c[i] / c[i-k] - 1 for i in range(k, len(c)) if c[i-k] > 0)
    n = int(len(r) * 0.05)
    if n < 5:
        return None, n
    return sum(r[:n]) / n * 100, n


def underwater(dates, closes):
    """고점을 되찾는 데 걸린 기간(달력 일수). 반환: (끝난 회복 중 최장, 지금 미회복 기간)"""
    day = datetime.date.fromisoformat
    peak = closes[0]; pi = 0; longest = 0
    for i, c in enumerate(closes):
        if c >= peak:
            if i - pi > 1:
                longest = max(longest, (day(dates[i]) - day(dates[pi])).days)
            peak = c; pi = i
    return longest, (day(dates[-1]) - day(dates[pi])).days


def soxl_crash(a, b, days):
    """SOXL 수익률 하위 10%인 날, 이 종목이 얼마나 같이 빠지나.
       반환: (그날 이 종목 평균 %, 그날 SOXL 평균 %, 그날끼리의 상관, 일수) — 20일 미만이면 None"""
    pr = []
    for d0, d1 in zip(days, days[1:]):
        if d0 in b and d1 in b and b[d0] > 0:
            pr.append((b[d1] / b[d0] - 1, d0, d1))
    pr.sort()
    cut = pr[:int(len(pr) * 0.10)]
    xs, ys = [], []
    for rb, d0, d1 in cut:
        if d0 in a and d1 in a and a[d0] > 0:
            xs.append(a[d1] / a[d0] - 1); ys.append(rb)
    n = len(xs)
    if n < 20:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    return mx * 100, my * 100, (sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None), n


def yellow(B, P=None, base=None):
    """노란 점 계산이 실패해도 종가 수집은 계속한다"""
    try:
        return market.yellow(B, P, base)
    except Exception as e:
        print(f'  노란 점 계산 실패: {e}')
        return None


def record(h):
    """일봉 하나로 종목 카드 값을 만든다"""
    dates = [b['d'] for b in h]
    closes = [b['c'] for b in h]
    vols = [b['v'] for b in h]
    tv = [c * v for c, v in zip(closes[-20:], vols[-20:]) if v]   # 최근 20일 거래대금
    peak = max(closes)
    peak1y = max(closes[-250:])
    s1, sn1 = sigma(closes, 250)
    s2, _ = sigma(closes, 60)
    es1, esn = tail_loss(closes, 1)
    es5, _ = tail_loss(closes, 5)
    uwMax, uwNow = underwater(dates, closes)
    r14 = market.rsi_w(closes)[-1] if len(closes) > 14 else None
    return {
        'price': round(closes[-1], 2),
        'date': dates[-1],
        'chg': round((closes[-1]/closes[-2] - 1) * 100, 2) if len(closes) > 1 else None,
        'rsi': round(r14, 1) if r14 is not None else None,
        'sig': round(s1, 2) if s1 is not None else None,
        'sigN': sn1,
        'sig60': round(s2, 2) if s2 is not None else None,
        'dd': round((closes[-1]/peak - 1) * 100, 1),
        'peak': round(peak, 2),
        'dd1y': round((closes[-1]/peak1y - 1) * 100, 1),
        'peak1y': round(peak1y, 2),
        'low1y': round(min(closes[-250:]), 2),                   # 1년 최저 종가 (1년 범위 막대)
        'mddCalc': round(maxdd(closes), 1),
        'bars': len(closes),
        'dv': round(sum(tv) / len(tv) / 1e6, 1) if tv else None,   # 하루 평균 거래대금 ($M)
        # 급락 위험 — 최근 3년 공통 구간 (상장이 짧으면 있는 만큼, riskBars로 표시)
        'es1': round(es1, 1) if es1 is not None else None,    # 하루 수익률 하위 5% 평균
        'es5': round(es5, 1) if es5 is not None else None,    # 5거래일 누적 수익률 하위 5% 평균
        'esN': esn,                                           # 하위 5%에 든 날 수
        'mdd3y': round(maxdd(closes[-RISK_N:]), 1),
        'riskBars': min(len(closes), RISK_N),
        'uwMax': uwMax,                                       # 과거 최장 회복 기간 (달력 일수)
        'uwNow': uwNow,                                       # 지금 역대 고점 밑에 머문 기간
    }


def main():
    print(f'대상 {len(TICKERS)}종목 (ETF {len(BASE)} + 추가 {len(EXTRA)})')
    if EXTRA:
        print('추가 종목:', ' '.join(EXTRA))

    # 지난 수집 결과 — 이번에 실패한 종목은 이 값을 그대로 둔다
    try:
        prev = json.load(open('data.json', encoding='utf-8')).get('data', {})
    except Exception:
        prev = {}

    data = {}
    series = {}   # 상관 계산용 {티커: {날짜: 종가}}
    hist = {}     # 노란 점을 기초자산으로 다시 판정할 때 쓰는 상품 일봉
    adj = {}      # 기초자산 대비 비교용 {티커: {날짜: 수정종가}} 최근 1년 남짓
    for tk in TICKERS:
        h = market.fetch(tk)
        if not h:
            print(f'{tk}: 데이터 없음' + (' — 지난 값 유지' if tk in prev else ''))
            continue
        rec = data[tk] = record(h)
        series[tk] = {b['d']: b['c'] for b in h}
        adj[tk] = {b['d']: b['a'] for b in h[-260:]}
        rec['dip'] = yellow(h)
        hist[tk] = h
        print(f"{tk}: ${rec['price']} RSI {rec['rsi']} σ {rec['sig']}%({rec['sigN']}일) 역대 {rec['dd']}% 1년 {rec['dd1y']}% 거래대금 ${rec['dv']}M")
        time.sleep(0.4)

    # 최근 1년(SOXL 거래일 기준) 상관 — SOXL 상관, 서로 비슷하게 움직이는 종목(0.7 이상)
    if 'SOXL' in series:
        days = sorted(series['SOXL'])[-251:]
        tks = list(series)
        for tk in tks:
            if tk == 'SOXL':
                continue   # 기준 종목 자신은 상관을 매기지 않는다
            c = corr(series[tk], series['SOXL'], days)
            data[tk]['corr'] = round(c, 2) if c is not None else None
        for i, a in enumerate(tks):
            for b in tks[i+1:]:
                c = corr(series[a], series[b], days)
                if c is not None and c >= 0.7:
                    data[a].setdefault('peers', []).append([b, round(c, 2)])
                    data[b].setdefault('peers', []).append([a, round(c, 2)])
        for tk in tks:
            if 'peers' in data[tk]:
                data[tk]['peers'].sort(key=lambda x: -x[1])
        print('SOXL 상관:', ' '.join(f"{t} {data[t].get('corr')}" for t in tks))

        # SOXL 급락일(최근 3년 하위 10%) 동반 하락
        days3 = sorted(series['SOXL'])[-(RISK_N + 1):]
        for tk in tks:
            if tk == 'SOXL':
                continue
            x = soxl_crash(series[tk], series['SOXL'], days3)
            if x:
                data[tk]['crash'] = {'me': round(x[0], 1), 'soxl': round(x[1], 1),
                                     'corr': round(x[2], 2) if x[2] is not None else None, 'n': x[3]}

    # 기초자산 — 노란 점 판정, 회복 여부, 최근 1년 수익률 비교(수정종가 기준, 두 종목 모두 값이 있는 날만)
    refH = {}
    for tk, (ref, lev) in REF.items():
        if tk not in adj:
            continue
        if ref not in refH:
            refH[ref] = market.fetch(ref)
            time.sleep(0.4)
        if not refH[ref]:
            continue                       # 기초자산을 못 받으면 상품 자체 판정을 그대로 둔다
        y = yellow(refH[ref], hist[tk], ref)
        if y:
            data[tk]['dip'] = y
        refh = {b['d']: b['a'] for b in refH[ref][-260:]}
        ds = sorted(d for d in adj[tk] if d in refh)[-251:]
        if len(ds) < 60:
            continue
        base = refh[ds[-1]] / refh[ds[0]] - 1
        prod = adj[tk][ds[-1]] / adj[tk][ds[0]] - 1
        data[tk]['ref'] = {'t': ref, 'lev': lev, 'base': round(base * 100, 1), 'prod': round(prod * 100, 1),
                           'gap': round((prod - lev * base) * 100, 1), 'n': len(ds) - 1}
    # 회복 여부 — ETF는 기초자산 고점 대비(레버리지 상품은 가치가 깎여 자기 고점으로는 거의 못 돌아간다).
    # 기초자산이 없는 ETF는 자기 가격으로 본다. 개별주는 화면에서 자기 가격으로 판정한다
    for tk in BASE:
        if tk not in data:
            continue
        ref = REF.get(tk, (None,))[0]
        data[tk]['rec'] = market.recovery(refH.get(ref), ref) if ref and refH.get(ref) else market.recovery(hist[tk])
    print('노란 점 켜짐:', ' '.join(t for t in data if (data[t].get('dip') or {}).get('on')) or '없음')
    print('기초자산 대비:', ' '.join(f"{t} {data[t]['ref']['gap']:+}%p" for t in data if 'ref' in data[t]))
    print('회복 여부:', ' '.join(f"{t} {data[t]['rec']['v']}" for t in BASE if data.get(t, {}).get('rec')))

    fresh = len(data)
    if fresh < len(TICKERS) / 2:
        # 데이터 출처가 통째로 막힌 날 — 빈 data.json으로 덮어쓰지 않는다
        print(f'\n수집 {fresh}/{len(TICKERS)}종목뿐이라 저장하지 않고 중단')
        sys.exit(1)
    for tk in TICKERS:
        if tk not in data and tk in prev:
            data[tk] = {**prev[tk], 'stale': True}

    out = {
        'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='minutes'),
        'count': len(data),
        'data': data,
    }
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    stale = [t for t in TICKERS if data.get(t, {}).get('stale')]
    missing = [t for t in TICKERS if t not in data]
    print(f'\n완료 — 새로 받음 {fresh}/{len(TICKERS)}종목')
    if stale:
        print('지난 값 유지:', ' '.join(stale))
    if missing:
        print('실패:', ' '.join(missing))


if __name__ == '__main__':
    main()
