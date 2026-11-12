#!/usr/bin/env python3
"""개별주 재무 경고 — fund.json 생성

tickers.txt의 개별주에 대해 SEC 공시(XBRL companyfacts) 숫자만으로 두 가지를 낸다.
  · 현금 여유 기간: (현금·단기투자 − 1년 내 갚을 빚) ÷ 월 현금 소진
      월 현금 소진은 최근 4분기 평균과 최근 분기 중 더 빠른 쪽을 쓴다(일회성·계절성 악화를 놓치지 않게).
      둘 다 잉여현금흐름이 0 이상이면 '현금 창출'로 본다.
  · 주식 수 증가: 최근 분기 가중평균 주식 수 ÷ 1년 전 같은 분기 − 1
      같은 공시 안의 비교 수치끼리 견주므로 분할·병합은 이미 맞춰져 있다.
투자 약정, 증자 목적, 가이던스처럼 공시 본문에 글로 적힌 내용은 다루지 않는다.
미국식 분기 공시(us-gaap)가 없는 회사와 펀드는 '미확인'으로 남긴다.
"""
import urllib.request, json, time, datetime, os, sys

# SEC는 연락처가 들어간 User-Agent를 요구한다
UA = {'User-Agent': 'witchveronica88-screener witchveronica88@users.noreply.github.com'}
STALE_DAYS = 200      # 마지막 공시 기준일이 이보다 오래되면 '공시 오래됨'
day = datetime.date.fromisoformat


def get(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:
            print(f'  SEC 실패 ({attempt+1}/3) {url.rsplit("/", 1)[-1]}: {e}')
            time.sleep(3)
    return None


def tickers():
    out = []
    for line in open('tickers.txt', encoding='utf-8'):
        t = line.split('#')[0].strip().upper()
        if t and t not in out:
            out.append(t)
    return out


# ───────────── 공시 숫자 꺼내기 ─────────────
def rows(facts, tags, unit='USD'):
    """여러 태그 중 가장 최근 기준일을 가진 태그의 항목들"""
    g = facts.get('facts', {}).get('us-gaap', {})
    best = []
    for t in tags:
        r = g.get(t, {}).get('units', {}).get(unit, [])
        if r and (not best or max(x['end'] for x in r) > max(x['end'] for x in best)):
            best = r
    return best


def instant(facts, tags, at=None):
    """시점 값(잔액). at이 있으면 그 날짜 값만. 반환: (값, 기준일) 또는 (None, None)"""
    r = [x for x in rows(facts, tags) if 'start' not in x]
    if at:
        r = [x for x in r if x['end'] == at]
    if not r:
        return None, None
    end = max(x['end'] for x in r)
    x = max((x for x in r if x['end'] == end), key=lambda x: x.get('filed', ''))
    return x['val'], end


def spans(facts, tags, unit='USD'):
    """기간 값 {(시작, 끝): 항목} — 같은 기간이 여러 공시에 있으면 나중 공시 것"""
    d = {}
    for x in rows(facts, tags, unit):
        if 'start' not in x:
            continue
        k = (x['start'], x['end'])
        if k not in d or x.get('filed', '') > d[k].get('filed', ''):
            d[k] = x
    return d


def near(a, b, tol):
    return abs((day(a) - day(b)).days) <= tol


def ttm_mrq(d, end=None):
    """누적(YTD)으로 공시되는 현금흐름에서 최근 4분기 합과 최근 분기 값을 구한다.
       반환: (4분기 합, 최근 분기, 기준일) — 못 구한 값은 None"""
    if not d:
        return None, None, None
    E = end or max(e for _, e in d)
    cand = [(s, e) for s, e in d if e == E and (day(e) - day(s)).days <= 372]
    if not cand:
        return None, None, E
    S, _ = min(cand)                                  # 가장 긴 누적 기간
    n = (day(E) - day(S)).days
    ytd = d[(S, E)]['val']
    prevq = next((v['val'] for (s, e), v in d.items()          # 한 분기 앞까지의 누적
                  if s == S and 75 <= (day(E) - day(e)).days <= 105), None)
    if n >= 350:                                      # 연간 공시
        return ytd, (ytd - prevq if prevq is not None else None), E
    mrq = ytd if n <= 105 else (ytd - prevq if prevq is not None else None)
    one = datetime.timedelta(days=1)
    fy = next((v['val'] for (s, e), v in d.items()
               if near(e, (day(S) - one).isoformat(), 4) and (day(e) - day(s)).days >= 350), None)
    yr = datetime.timedelta(days=365)
    pytd = next((v['val'] for (s, e), v in d.items()
                 if near(s, (day(S) - yr).isoformat(), 8) and near(e, (day(E) - yr).isoformat(), 8)), None)
    ttm = ytd + fy - pytd if fy is not None and pytd is not None else None
    return ttm, mrq, E


def share_growth(facts):
    """가중평균 주식 수의 1년 증가율(%). 같은 공시 안의 당기·전년 동기 비교. 반환: (증가율, 기준일)"""
    d = spans(facts, ['WeightedAverageNumberOfSharesOutstandingBasic'], 'shares')
    if not d:
        return None, None
    E = max(e for _, e in d)
    cur = min(((s, e) for s, e in d if e == E), key=lambda k: (day(k[1]) - day(k[0])).days)   # 가장 짧은 기간(분기)
    n = (day(cur[1]) - day(cur[0])).days
    accn = d[cur].get('accn')
    yr = datetime.timedelta(days=365)
    for x in rows(facts, ['WeightedAverageNumberOfSharesOutstandingBasic'], 'shares'):
        if ('start' in x and x.get('accn') == accn and near(x['end'], (day(E) - yr).isoformat(), 10)
                and abs((day(x['end']) - day(x['start'])).days - n) <= 10 and x['val'] > 0):
            return (d[cur]['val'] / x['val'] - 1) * 100, E
    return None, E


CASH_ALL = ['CashCashEquivalentsAndShortTermInvestments']
CASH = ['CashAndCashEquivalentsAtCarryingValue']
INVEST = ['ShortTermInvestments', 'MarketableSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent',
          'DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent']
DEBT_ALL = ['DebtCurrent']
DEBT_PARTS = ['LongTermDebtCurrent', 'ConvertibleNotesPayableCurrent', 'ShortTermBorrowings',
              'NotesPayableCurrent', 'ConvertibleDebtCurrent', 'LinesOfCreditCurrent']
OCF = ['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations']
CAPEX = ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets']
M = 1e6


def analyze(facts, today):
    name = facts.get('entityName', '')
    if 'us-gaap' not in facts.get('facts', {}):
        return {'name': name, 'na': '미국식 분기 공시(us-gaap) 자료가 없습니다'}
    if any(w in name.upper() for w in (' TRUST', ' ETF', ' FUND')):
        return {'name': name, 'na': '펀드·신탁이라 기업 재무를 적용하지 않습니다'}
    cash, asof = instant(facts, CASH)
    if cash is None:
        return {'name': name, 'na': '현금 항목을 찾지 못했습니다'}
    both, _ = instant(facts, CASH_ALL, asof)
    if both is not None:
        cash = both
    else:
        for t in INVEST:
            v, _ = instant(facts, [t], asof)
            if v is not None:
                cash += v
                break
    debt, _ = instant(facts, DEBT_ALL, asof)
    if debt is None:
        debt = sum(v for v in (instant(facts, [t], asof)[0] for t in DEBT_PARTS) if v is not None)
    ocfT, ocfQ, E = ttm_mrq(spans(facts, OCF))
    cap = spans(facts, CAPEX)
    capT, capQ, _ = ttm_mrq(cap, E) if cap else (None, None, None)
    fcfT = ocfT - (capT or 0) if ocfT is not None else None
    fcfQ = ocfQ - (capQ or 0) if ocfQ is not None else None
    sh, shAsof = share_growth(facts)
    r = {'name': name, 'asof': asof, 'cash': round(cash / M, 1), 'debt': round(debt / M, 1),
         'fcfT': round(fcfT / M, 1) if fcfT is not None else None,
         'fcfQ': round(fcfQ / M, 1) if fcfQ is not None else None,
         'cfAsof': E, 'sh': round(sh, 1) if sh is not None else None, 'shAsof': shAsof,
         'old': (today - day(asof)).days > STALE_DAYS}
    burns = [b for b in (-fcfT / 12 if fcfT is not None else None, -fcfQ / 3 if fcfQ is not None else None)
             if b is not None]
    if burns:
        burn = max(burns)                              # 더 빠른 소진 속도
        if burn <= 0:
            r['self'] = True                           # 4분기·최근 분기 모두 현금 창출
        else:
            r['burn'] = round(burn / M, 1)             # 월 소진 ($M)
            r['runway'] = round(max(cash - debt, 0) / burn, 1)
    return r


def main():
    tks = tickers()
    m = get('https://www.sec.gov/files/company_tickers.json')
    if not m:
        print('SEC 종목 목록을 받지 못해 중단 — fund.json은 그대로 둡니다')
        sys.exit(1)
    cik = {v['ticker'].upper(): v['cik_str'] for v in m.values()}
    try:
        prev = json.load(open('fund.json', encoding='utf-8')).get('data', {})
    except Exception:
        prev = {}
    today = datetime.date.today()
    data = {}
    for tk in tks:
        if tk not in cik:
            print(f'{tk}: SEC 목록에 없음 (ETF 등)')
            continue
        facts = get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[tk]:010d}.json')
        time.sleep(0.3)
        if not facts:
            if tk in prev:
                data[tk] = prev[tk]                    # 이번에 못 받으면 지난 값 유지
            print(f'{tk}: 받지 못함' + (' — 지난 값 유지' if tk in prev else ''))
            continue
        try:
            data[tk] = analyze(facts, today)
        except Exception as e:
            data[tk] = {'name': facts.get('entityName', ''), 'na': '계산하지 못했습니다'}
            print(f'{tk}: 계산 오류 {e!r}')
        print(f'{tk}: {json.dumps(data[tk], ensure_ascii=False)}')
    out = {'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='minutes'), 'data': data}
    with open('fund.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'\n완료 — {len(data)}종목')


if __name__ == '__main__':
    main()
