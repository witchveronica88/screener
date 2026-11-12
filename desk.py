#!/usr/bin/env python3
"""반등·과열 타이밍 상황판 — desk.json 생성 (Rule v2.3)

v2.3: '매수 기회 전광판'과 '오늘의 해석'을 더했다. 기존 🟢🟡 판정은 그대로다.
      전광판은 사용자 운영 설정이다 — 노란 점(세 지표가 모두 '많이 빠짐'인 날)을 매수 기회로 본다.
      되살아나는 신호를 기다리는 기존 판정보다 이르게 켜지므로, 과거 노란 점 다음 날 샀을 때의
      결과(목표 도달률·가는 길 하락)를 전광판에 같이 적는다.

v2.1: 주봉·월봉 3단 하향 이탈은 ⚠️ 경계로 올리지 않고 남은 위험으로만 표시한다.
      과거 신호에서 그 조건의 목표 도달률이 전체와 차이가 없었다(SOXL 60% vs 65%, TQQQ 67% vs 59%).
v2.2: VIX '빠른 완화 관찰'(초기 반등형)은 🟢 매수 검토가 아니라 🟡 매수 관찰로 둔다.
      신호일 VIX 상태별 +10% 도달 — 확인형 50/75건(67%), 초기 반등형 15/31건(48%)
      (SOXL·TQQQ·UPRO·TNA·TECL 합계). VIX가 확인형으로 바뀌면 신규 3거래일 안에서 🟢로 올라간다.

기준자산(SOXX·QQQ)의 확정 일봉으로 SCR 3단 신호를 판정하고, VIX 완화/악화,
주봉·월봉 상위 추세, UVIX 사이클을 합쳐 '현재 판단' 카드를 만든다.
어제 판정은 마지막 봉을 뺀 자료로 같은 계산을 한 번 더 돌려 구한다(미래 참조 금지).
"""
import urllib.request, csv, io, json, time, datetime, sys
from market import (UA, fetch, sma, stoch_k, cci, rsi_w, boll, indicators,   # 가격 받기 · 지표 · 노란 점은 종가 수집과 같이 쓴다
                    is_dip, dip_past, TARGET, DIP_DAYS)

RULE = 'Rule v2.3'
PAIRS = [('SOXL', 'SOXX'), ('TQQQ', 'QQQ')]   # (관찰 상품, 주 신호 계산 기준자산)
SYMS = ['SOXX', 'SOXL', 'QQQ', 'TQQQ', 'UVIX', 'VXX', 'SPY', '^VIX']
NEW_DAYS = 3      # 신규 신호 강조: 발생일 포함 3거래일 (D+0~D+2)
WINDOW = 10       # 준비 이후 순차 회복·이탈 허용 거래일


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
    # 분할·병합 직후 보정이 덜 된 자료로 잘못 판정하지 않게 막는다
    if (abs(B[-1]['c'] / B[-2]['c'] - 1) > 0.25
            or any(not (b['l'] <= b['c'] <= b['h']) for b in B[-5:])):
        return {'asof': asof, 'state': 'wait', 'label': '⏳ 데이터 대기·판정 불가',
                'line': '기준자산 가격이 하루 만에 크게 달라졌습니다. 분할·병합 보정이 끝났는지 확인 중입니다.'}
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
    weak = bool((wk and wk['weak']) or (mo and mo['weak'])) or serious
    R['weak'] = weak; R['serious'] = serious

    # 판정 우선순위: 데이터 → 위험 경고 → VIX 완화 수준 → 개별 신호
    if up_live:
        if vx['st'] == 'worse':
            st, lb = 'warn', '⚠️ 신규 진입 경계'
            ln = '단기 반등 신호가 있으나 시장 변동성 위험이 커 신규 진입은 경계가 필요합니다.'
            ln += ' (원인: ' + vx['why'] + ')'
        elif vx['st'] == 'fast':
            st, lb = 'watch', '🟡 매수 관찰 · 초기 반등형(확인 대기)'
            R['type'] = '초기 반등형'
            ln = '과매도 회복과 함께 변동성 초기 진정 조짐이 나타났지만 아직 확인 전입니다. (VIX 완화 확인까지 대기)'
        elif vx['st'] == 'confirm':
            st = 'buy'
            typ = '확인형'
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
        R['live'] = bool(up and R['sig']['valid'] and R['sig']['age'] <= 60 and not (dn and dn['i'] > up['i']))
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


def easy_state(t):
    """판정을 쉬운 말로 — (제목, 한 줄 설명). 매수 기회 전광판(노란 점)과는 따로 움직인다"""
    st = t['state']; s = t.get('sig')
    if st == 'wait':
        return '⏳ 자료 확인 중', t['line']
    if st == 'buy':
        return ('🟢 매수 검토해 볼 만함', '많이 빠졌다가 되살아났고, 시장 불안도 가라앉았습니다.'
                + (' 다만 큰 흐름은 아직 약세라 짧게 보는 편이 안전합니다.' if t.get('weak') else ''))
    if st == 'warn':
        return '⚠️ 반등 신호는 났지만 보류', '가격은 되살아났는데 시장 불안(VIX)이 다시 커지고 있습니다.'
    if st == 'watch':
        if s and s['new'] and s['valid']:
            return ('🟡 되살아났지만 더 지켜보기',
                    '가격은 되살아났지만 시장 불안이 이제 막 꺾이는 중이라 확인이 덜 됐습니다. '
                    '불안이 확실히 가라앉으면 매수 검토로 바뀝니다.' if t.get('type') == '초기 반등형'
                    else '가격은 되살아났는데 시장 불안이 아직 풀리지 않았습니다.')
        return '🟡 많이 빠짐 — 더 지켜보기', '많이 내려왔지만 반등 신호는 아직 없습니다.'
    if st == 'hot':
        return '🟠 많이 올라 과열 — 추격 조심', '많이 올랐지만 아직 꺾이지는 않았습니다. 새로 사기에는 부담스러운 자리입니다.'
    if st == 'sell':
        return '🔴 과열 뒤 꺾임', '많이 오른 뒤 힘이 빠지는 신호가 나왔습니다. 새로 사는 것은 미루는 편이 낫습니다.'
    if t.get('live'):
        return '⚪ 뚜렷한 신호 없음', f"{s['age']}거래일 전에 나온 반등 신호가 아직 살아 있습니다. 새 신호는 없습니다."
    return '⚪ 뚜렷한 신호 없음', '사야 할 신호도, 과열 신호도 없습니다.'


def next_states(base, t, vx):
    """앞으로 어떤 일이 생기면 판단이 바뀌는지 — '조건 → 결과' 쉬운 말"""
    out = []
    sg = t.get('sig'); ind = t['ind']
    if sg and sg['valid'] and sg['age'] <= 60:
        out.append(f"{base} 종가가 ${sg['low']} 아래로 내려가면 → 반등 신호 취소")
    if t['state'] in ('buy', 'watch') and sg and sg['new'] and sg['valid']:
        if t.get('type') != '확인형':
            out.append(f"VIX가 이틀 연속 내리고 5일 평균({vx['m5']}) 아래로 가면 → 🟢 매수 검토")
        out.append('VIX가 하루에 10% 넘게 뛰거나, 이틀 연속 올라 5일 평균을 넘으면 → ⚠️ 보류')
        if t.get('weak'):
            out.append("주 단위 지표(CCI 0 · RSI 50)가 회복되면 → '큰 흐름 약세' 주의가 사라짐")
    if t.get('setupUp'):
        out.append(f"남은 {t['setupUp']['left']}거래일 안에 세 지표가 모두 기준선을 넘으면 → 반등 신호 "
                   f"(지금 %K {ind['k']} / 20 · CCI {ind['cci']} / -100 · RSI {ind['rsi']} / 30)")
    if t['state'] == 'hot':
        if t.get('setupDn'):
            out.append('세 지표가 모두 과열 기준선 아래로 내려오면 → 🔴 과열 뒤 꺾임')
        else:
            out.append(f"50일 평균보다 5% 안쪽으로 내려오면 → ⚪ 신호 없음 (지금 {ind['disp'] - 100:+.1f}%)")
    if t['state'] == 'neutral' and not t.get('setupUp'):
        out.append("세 지표가 같은 날 모두 '많이 빠짐'에 들어가면 → 🟡 지켜보기 시작")
        out.append(f"50일 평균보다 5% 넘게 올라가면 → 🟠 과열 (지금 {ind['disp'] - 100:+.1f}%)")
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
    r = {'asof': U[-1]['d'], 'close': round(c[-1], 2), 'chg': pct(c[-1], c[-2]), 'lastDone': lastDone,
         'jump': not 0.4 < c[-1] / c[-2] < 3}       # 역병합 보정이 덜 됐을 수 있는 급변
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
    r['spark'] = [{'d': b['d'], 'c': round(b['c'], 2)} for b in U[-21:]]
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
    vc5 = sma([b['c'] for b in V], 5)
    r['spark'] = [{'d': V[i]['d'], 'c': round(V[i]['c'], 2), 'm5': round(vc5[i], 2)}
                  for i in range(len(V) - 21, len(V))]
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
                    'state': t['state'], 'label': t['label'], 'change': change_reason(y, t),
                    'title': easy_state(t)[0], 'easy': plain_reason(y, t), 'marks': marks})
    return out


# ───────────── 성적표 · 이벤트 로그 · 요약표 · 판정 기록 ─────────────
COST = 0.2        # 왕복 거래비용 가정 (%)


def div_all(B, I):
    rs = I['rsi']; piv = []
    for i in range(3, len(B) - 3):
        w = rs[i-3:i+4]
        if None not in w and all(rs[i] < x for j, x in enumerate(w) if j != 3):
            piv.append(i)
    return [(a, b) for a, b in zip(piv, piv[1:])
            if 5 <= b - a <= 60 and B[b]['l'] < B[a]['l'] and rs[b] > rs[a]]


def simulate(s, B, P, pidx, bidx, target):
    """신호 확정 다음 거래일 시가에 관찰 상품 진입을 가정.
       목표: 상품 장중 고가 ≥ 진입가×target / 무효화: 기준자산 종가 < 관찰 저점
       같은 날 둘 다면 '장중 순서 불명'(보수적으로 미달 처리)"""
    pi = pidx.get(B[s['i']]['d'])
    if pi is None or pi + 1 >= len(P):
        return None
    entry = P[pi + 1]['o']; mae = 0.0
    for j in range(pi + 1, len(P)):
        mae = min(mae, P[j]['l'] / entry - 1)
        bj = bidx.get(P[j]['d'])
        hit = P[j]['h'] >= entry * target
        inv = bj is not None and B[bj]['c'] < s['ext']
        if hit or inv:
            return {'res': 'amb' if hit and inv else 'hit' if hit else 'inv', 'days': j - pi,
                    'date': P[j]['d'], 'mae': mae * 100, 'entry': entry}
    return {'res': 'open', 'days': len(P) - 1 - pi, 'date': None, 'mae': mae * 100, 'entry': entry}


def backtest(base, prod, bars):
    B, P, V = bars[base], bars[prod], bars['^VIX']
    I = indicators(B); sigs, _ = scr_signals(B, I)
    pidx = {b['d']: i for i, b in enumerate(P)}; bidx = {b['d']: i for i, b in enumerate(B)}
    vidx = {b['d']: i for i, b in enumerate(V)}
    divs = div_all(B, I)
    rows = []
    for s in sigs:
        d = B[s['i']]['d']
        if s['side'] != 'up' or d < P[0]['d']:
            continue
        v = vidx.get(d)
        vs = vix_state(V[v-30:v+1])['st'] if v is not None and v >= 30 else None
        r = simulate(s, B, P, pidx, bidx, 1 + TARGET / 100)
        rn = simulate(s, B, P, pidx, bidx, 1 + (TARGET + COST) / 100)
        pi = pidx.get(d)
        Bs = B[:s['i'] + 1]; wk = upper_trend(Bs, 'w'); mo = upper_trend(Bs, 'm')   # 신호일까지의 자료만
        rows.append({'s': s, 'd': d, 'strict': s['strict'], 'vix': vs,
                     'weak': bool((wk and wk['weak']) or (mo and mo['weak'])),
                     'serious': bool((wk and wk['brk']) or (mo and mo['brk'])),
                     'div': any(b >= s['start'] - 5 and b + 3 <= s['i'] for a, b in divs),
                     'gap': (r['entry'] / P[pi]['c'] - 1) * 100 if r else None, 'r': r, 'rn': rn})
    return rows, sigs, divs, B, P, pidx


def stats(cat, rows, group=None):
    rs = [x for x in rows if x['r']]
    n = len(rs)
    out = {'cat': cat, 'g': group, 'n': n}
    if not n:
        return out
    hit = [x for x in rs if x['r']['res'] == 'hit']
    days = sorted(x['r']['days'] for x in hit)
    out.update(hit=round(len(hit) / n * 100), hitNet=round(sum(x['rn']['res'] == 'hit' for x in rs) / n * 100),
               avgD=round(sum(days) / len(days), 1) if days else None,
               medD=(days[(len(days) - 1) // 2] + days[len(days) // 2]) / 2 if days else None,
               maeAvg=round(sum(x['r']['mae'] for x in rs) / n, 1), maeMax=round(min(x['r']['mae'] for x in rs), 1),
               inv=sum(x['r']['res'] == 'inv' for x in rs), amb=sum(x['r']['res'] == 'amb' for x in rs),
               open=sum(x['r']['res'] == 'open' for x in rs))
    return out


def scorecard(rows):
    f = lambda fn: [x for x in rows if fn(x)]
    gp = lambda lo, hi: f(lambda x: x['gap'] is not None and lo <= x['gap'] < hi)
    return [stats('전체', rows),
            stats('세 지표가 같은 날 한꺼번에 회복', f(lambda x: x['strict']), '되살아난 방식'),
            stats('세 지표가 며칠에 걸쳐 차례로 회복', f(lambda x: not x['strict'])),
            stats('시장 불안이 가라앉은 뒤', f(lambda x: x['vix'] == 'confirm'), '신호가 난 날의 시장 불안(VIX)'),
            stats('시장 불안이 막 꺾일 때', f(lambda x: x['vix'] == 'fast')),
            stats('시장 불안 보통', f(lambda x: x['vix'] == 'neutral')),
            stats('시장 불안이 커질 때', f(lambda x: x['vix'] == 'worse')),
            stats('내려서 시작', gp(-999, 0), '신호 다음 날 시작 가격 (전날 종가 대비)'),
            stats('0~2% 올라 시작', gp(0, 2)), stats('2~5% 올라 시작', gp(2, 5)), stats('5% 넘게 올라 시작', gp(5, 999)),
            stats('큰 흐름 양호', f(lambda x: not x['weak'] and not x['serious']), '신호가 난 날의 큰 흐름 (주·월 단위)'),
            stats('큰 흐름 약세', f(lambda x: x['weak'] and not x['serious'])),
            stats('주·월 단위로 과열 뒤 꺾인 상태', f(lambda x: x['serious'])),
            stats('바닥 힌트 있음', f(lambda x: x['div']), '바닥 힌트 (다이버전스: 가격은 더 빠졌는데 힘은 덜 빠짐)'),
            stats('바닥 힌트 없음', f(lambda x: not x['div']))]


def event_log(base, prod, rows, sigs, divs, B, P, pidx, n=10):
    ev = []; last = len(B) - 1; pc = P[-1]['c']
    for x in rows[-n:]:
        s, r = x['s'], x['r']
        e = {'d': x['d'], 'type': 'up', 'kind': '엄격 동시 회복' if x['strict'] else '순차 회복',
             'age': last - s['i'], 'sigClose': round(B[s['i']]['c'], 2), 'low': round(s['ext'], 2)}
        if r:
            end = pc if r['res'] == 'open' else None
            e.update(entry=round(r['entry'], 2), res=r['res'], days=r['days'], resDate=r['date'],
                     mae=round(r['mae'], 1), gap=round(x['gap'], 1))
            if end:
                e.update(cur=round(pc, 2), pnl=round((pc / r['entry'] - 1) * 100, 1),
                         pnlNet=round((pc / r['entry'] - 1) * 100 - COST, 1))
        else:
            e['res'] = 'pre'
        ev.append(e)
    for s in [s for s in sigs if s['side'] == 'dn'][-4:]:
        ev.append({'d': B[s['i']]['d'], 'type': 'dn', 'kind': '엄격 동시 이탈' if s['strict'] else '순차 이탈',
                   'age': last - s['i'], 'sigClose': round(B[s['i']]['c'], 2),
                   'mv': pct(B[-1]['c'], B[s['i']]['c'])})
    for a, b in divs[-3:]:
        if b + 3 <= last:
            ev.append({'d': B[b + 3]['d'], 'type': 'div', 'kind': 'RSI 상승 다이버전스 확인', 'age': last - b - 3,
                       'lowDate': B[b]['d'], 'sigClose': round(B[b + 3]['c'], 2),
                       'mv': pct(B[-1]['c'], B[b + 3]['c'])})
    ev.sort(key=lambda e: e['d'], reverse=True)
    return ev[:n]


def summary_row(sym, B, inv=None):
    if len(B) < 60:
        return {'sym': sym, 'na': True}
    I = indicators(B); c = B[-1]['c']
    pos = (c - I['bl'][-1]) / (I['bu'][-1] - I['bl'][-1]) * 100 if I['bu'][-1] != I['bl'][-1] else 50
    bb = ('상단 밖' if pos > 100 else '상단 근처' if pos >= 80 else '하단 밖' if pos < 0 else
          '하단 근처' if pos <= 20 else '중간')
    def tr(nm, u):
        if not u:
            return f'{nm} 흐름: 자료 부족'
        t = f"{nm} 흐름: <b>{'과열 뒤 꺾인 상태' if u['brk'] else '약세' if u['weak'] else '양호'}</b> <small>(CCI {u['cci']} · RSI {u['rsi']}"
        t += (f" / 아직 안 끝난 이번 {nm[0]} 값 CCI {u['prev']['cci']} · RSI {u['prev']['rsi']})</small>" if u['prev'] else ')</small>')
        return t
    return {'sym': sym, 'close': round(c, 2), 'chg': pct(c, B[-2]['c']), 'k': round(I['k'][-1], 1),
            'd': round(I['d'][-1], 1), 'cci': round(I['cci'][-1], 1), 'rsi': round(I['rsi'][-1], 1),
            'disp': round(I['disp'][-1], 1), 'bb': f'{bb} ({pos:.0f}%)',
            'wk': tr('주 단위', upper_trend(B, 'w')), 'mo': tr('월 단위', upper_trend(B, 'm')), 'inv': inv}


def save_log(out):
    """그날 실제로 표시한 판정을 보존한다. 이미 기록된 거래일은 덮어쓰지 않는다"""
    try:
        log = json.load(open('desk_log.json', encoding='utf-8'))
    except Exception:
        log = []
    if any(x['d'] == out['asof'] for x in log):
        return
    log.append({'d': out['asof'], 'rule': RULE, 'saved': out['updated'],
                'items': {c['prod']: {'label': c['label'], 'change': c.get('change', '')} for c in out['cards']},
                'uvix': out['uvix']['cycle']['stage'] if out['uvix'] else None,
                'vix': out['market']['vixLabel']})
    log.sort(key=lambda x: x['d'])
    with open('desk_log.json', 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


VIX_EASY = {'worse': '커짐', 'confirm': '가라앉음', 'fast': '막 꺾이는 중', 'neutral': '보통'}


def plain_reason(y, t):
    """요약 탭용 — 어제와 무엇이 달라졌는지 쉬운 말로"""
    if y['state'] == 'wait' or t['state'] == 'wait':
        return {'head': '어제와 비교할 자료가 부족합니다', 'why': []}
    why = []
    ys, ts = y.get('sig'), t.get('sig')
    fresh = ts and (not ys or ys['date'] != ts['date'])
    if fresh:
        why.append('많이 빠졌다가 반등 신호가 새로 나왔습니다')
    if ys and ts and ys['date'] == ts['date']:
        if ys['valid'] and not ts['valid']:
            why.append('가격이 기준 저점 아래로 내려가 반등 신호가 취소됐습니다')
        elif ys['new'] and not ts['new'] and ts['valid']:
            why.append('신호가 나온 지 3일이 지났습니다(신호는 아직 살아 있음)')
    td, yd = t.get('dnSig'), y.get('dnSig')
    if td and td['age'] == 0:
        why.append('과열 뒤 꺾이는 신호가 나왔습니다')
    elif td and yd and yd['age'] < NEW_DAYS <= td['age']:
        why.append('꺾임 신호가 나온 지 3일이 지났습니다')
    if t.get('setupUp') and not y.get('setupUp'):
        why.append("세 지표가 모두 '많이 빠짐'에 들어갔습니다 (노란 점)")
    if y.get('setupUp') and not t.get('setupUp') and not fresh:
        why.append('10거래일 안에 되살아나지 못해 관찰을 끝냈습니다')
    if t.get('setupDn') and not y.get('setupDn'):
        why.append("세 지표가 모두 '많이 오름'에 들어갔습니다")
    if y['vix'] != t['vix']:
        why.append(f"시장 불안(VIX)이 '{VIX_EASY[y['vix']]}'에서 '{VIX_EASY[t['vix']]}'(으)로 바뀌었습니다")
    if y.get('weak') != t.get('weak'):
        why.append('큰 흐름(주·월 단위) 약세가 ' + ('시작됐습니다' if t.get('weak') else '풀렸습니다'))
    head = '어제와 같은 판단' if y['label'] == t['label'] else '어제와 판단이 달라짐'
    return {'head': head, 'why': why or ['달라진 조건은 없습니다']}


def plain_risks(t, vxf):
    out = []
    for nm, u in (('주 단위', t.get('wk')), ('월 단위', t.get('mo'))):
        if u and u['brk']:
            out.append(f'{nm}로 과열 뒤 꺾인 상태')
        elif u and u['weak']:
            out.append(f'{nm} 흐름이 아직 약세')
    if not vxf:
        out.append('VIX 선물 자료를 확인하지 못함')
    elif vxf['back']:
        out.append('선물 시장이 단기 불안을 반영 중')
    if t.get('sig') and t['sig']['new'] and t['sig']['hot']:
        out.append('신호 당일 이미 단기 급등')
    return out


def past_like(t, rows):
    """지금과 같은 조건이던 과거 회복 신호의 결과 (요약 탭 근거 한 줄)"""
    v = t['vix']
    sel = [x for x in rows if x['vix'] == v]
    nm = f"시장 불안(VIX)이 '{VIX_EASY[v]}'일 때 나온 반등 신호"
    st = stats(nm, sel)
    return st if st['n'] else None


# ───────────── 매수 기회 전광판 · 오늘의 해석 ─────────────
def dip_card(t, base, prod, bars):
    """전광판 상태와 '오늘의 해석' — 왜 지금이 매수 기회인지(아닌지)를 쉬운 말로"""
    B = bars[base]; I = indicators(B); last = len(B) - 1
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
    lo = min(range(hi, len(w)), key=lambda i: w[i]['c'])          # 고점 뒤 가장 낮았던 날
    drop = pct(w[lo]['c'], w[hi]['c']); days = lo - hi
    spy = pct(S[w[lo]['d']], S[w[hi]['d']]) if w[lo]['d'] in S and w[hi]['d'] in S else None
    back = pct(B[-1]['c'], w[lo]['c']) if lo < len(w) - 1 else None   # 저점 뒤 되오른 폭
    r['drop'] = {'pct': drop, 'days': days, 'from': w[hi]['d'], 'to': w[lo]['d'], 'spy': spy, 'back': back}
    st = t['state']; lastd = r.get('last'); vix = t['vix']; sig = t.get('sig')
    md = lambda d: f"{int(d[5:7])}/{int(d[8:10])}"
    why = []
    if days and drop <= -3:
        why.append(f"{base}가 {md(w[hi]['d'])} 고점에서 {days}거래일 만에 <b>{drop}%</b> 내렸습니다" +
                   (f" (저점 {md(w[lo]['d'])}, 그 뒤 {back:+.1f}%)" if back is not None else ''))
        if spy is not None:
            why.append(f"같은 기간 시장 전체(SPY)는 {spy:+.1f}% — " +
                       ('시장 전체가 함께 빠진 하락입니다' if spy <= drop / 3 else '시장보다 이 종목군이 유독 많이 빠졌습니다'))
    if on:
        head = f"지금은 매수 기회일 수 있습니다. {base}가 급하게 내려 세 지표가 모두 '많이 빠짐'에 들어갔습니다(노란 점)."
        why.append(f"노란 점 {run}일째입니다" if run > 1 else '오늘 노란 점이 새로 켜졌습니다')
        why.append({'worse': '시장 불안(VIX)이 아직 커지는 중이라 더 빠질 수 있습니다. 한 번에 다 사지 않는 편이 안전합니다',
                    'fast': '시장 불안(VIX)이 막 꺾이기 시작했습니다',
                    'confirm': '시장 불안(VIX)은 이미 가라앉고 있습니다',
                    'neutral': '시장 불안(VIX)은 보통 수준입니다'}[vix])
        why.append('반등 신호는 아직 없습니다. 바닥을 확인하고 사는 것이 아니라 내려오는 중에 사는 자리입니다')
    elif t.get('setupUp') and lastd:
        head = (f"매수 기회 구간이 이어지는 중일 수 있습니다. 노란 점은 {lastd['age']}거래일 전에 꺼졌고, "
                '아직 반등 신호는 없습니다.')
        why.append(f"마지막 노란 점({md(lastd['date'])}) 때보다 {base} <b>{lastd['mv']:+.1f}%</b>")
        why.append(f"{t['setupUp']['left']}거래일 안에 되살아나지 못하면 관찰이 끝납니다")
    elif st in ('buy', 'watch', 'warn') and sig and sig['valid'] and sig['new']:
        head = '급락 뒤 되살아나는 중입니다. 가장 싸게 살 수 있던 구간은 지났을 수 있습니다.'
        if lastd:
            why.append(f"마지막 노란 점({md(lastd['date'])}) 뒤로 이미 {base} <b>{lastd['mv']:+.1f}%</b>" +
                       (f" · {prod} <b>{lastd['pmv']:+.1f}%</b>" if lastd['pmv'] is not None else ''))
        why.append({'worse': '다만 시장 불안(VIX)이 다시 커지고 있어 한 번 더 빠질 수 있습니다',
                    'fast': '시장 불안(VIX)은 막 꺾이는 중입니다',
                    'confirm': '시장 불안(VIX)도 가라앉았습니다',
                    'neutral': '시장 불안(VIX)은 보통 수준입니다'}[vix])
        why.append(f"{base} 종가가 ${sig['low']} 아래로 가면 반등 신호가 취소됩니다")
    elif st in ('hot', 'sell'):
        head = ('지금은 매수 기회가 아닙니다. 많이 오른 뒤 힘이 빠지는 자리입니다.' if st == 'sell'
                else '지금은 매수 기회가 아닙니다. 많이 올라 과열된 자리입니다.')
        why = ['세 지표가 모두 \'많이 오름\' 쪽에 있거나 거기서 막 내려왔습니다']
    else:
        head = ("많이 내렸지만 아직 매수 기회 신호는 아닙니다. 세 지표가 모두 '많이 빠짐'에 들어가야 노란 점이 켜집니다."
                if drop <= -8 and back is None else '지금은 매수 기회 신호가 없습니다. 급하게 빠진 상태가 아닙니다.')
        ok = lambda v: ' ✓' if v else ''
        why.append(f"노란 점 조건: 빠르기 {r['k']} (20 이하{ok(r['k'] <= 20)}) · 벗어난 정도 {r['cci']} (-100 이하{ok(r['cci'] <= -100)})"
                   f" · 힘 {r['rsi']} (30 이하{ok(r['rsi'] <= 30)})")
        if lastd:
            why.append(f"마지막 노란 점은 {md(lastd['date'])} ({lastd['age']}거래일 전), 그 뒤 {base} {lastd['mv']:+.1f}%")
    if t.get('weak') and st not in ('hot', 'sell'):
        why.append('큰 흐름(주·월 단위)은 아직 약세라, 반등해도 짧게 끝날 수 있습니다')
    r['head'] = head; r['why'] = why
    return r


# ───────────── 흐름 단계 · 결론 한 문장 (요약 탭 첫 화면) ─────────────
STAGES = ['평소', '노란 점', '반등', '과열', '꺾임']
NAMES = {'SOXL': ('반도체(SOXL)', '는'), 'TQQQ': ('나스닥(TQQQ)', '은')}   # 결론 문장에 쓰는 이름과 조사


def md_(d):
    return f"{int(d[5:7])}/{int(d[8:10])}"


def stage_of(t):
    """흐름 단계 — 평소 → 노란 점 → 반등 → 과열 → 꺾임 중 지금 어디쯤인지.
       노란 점(매수 기회)과 🟢🟡 판정은 각자 계산한 그대로이고, 여기서는 한 줄에 놓기만 한다.
       반환: i(단계 번호, 자료가 없으면 None) · cls(색) · text(짧은 상태) · say(결론 문장에 쓰는 말) · lines(두세 줄)"""
    st, s, d = t['state'], t.get('sig'), t.get('dip') or {}
    if st == 'wait':
        return {'i': None, 'cls': 'wait', 'text': '자료 확인 중', 'say': '자료를 확인하는 중입니다', 'lines': []}
    live = s and s['valid'] and s['age'] <= 60
    if st == 'hot':
        r = (3, 'hot', '많이 올라 과열', '많이 올라 과열이라 추격은 조심할 자리입니다')
    elif st == 'sell':
        r = (4, 'sell', '과열 뒤 꺾임', '많이 오른 뒤 꺾였습니다')
    elif d.get('on'):
        r = (1, 'watch', f"노란 점 {d['run']}일째 · 바닥 확인 전",
             '노란 점이 켜져 싸게 살 수 있는 자리지만, 바닥을 확인하기 전입니다')
    elif st in ('buy', 'watch', 'warn') and s and s['new'] and s['valid']:
        n = s['age'] + 1
        r = {'buy': (2, 'buy', f'반등 {n}일째 · 확인됨', '급락 뒤 반등이 확인됐습니다'),
             'watch': (2, 'watch', f'반등 {n}일째 · 시장 불안 확인 대기', '반등 신호는 났지만 시장 불안이 아직 덜 가라앉았습니다'),
             'warn': (2, 'warn', f'반등 {n}일째 · 시장 불안 커져 보류', '반등 신호는 났지만 시장 불안이 다시 커져 보류입니다')}[st]
    elif t.get('setupUp'):
        r = (1, 'watch', '많이 빠진 뒤 반등 기다리는 중', '많이 빠진 뒤 반등을 기다리는 중입니다')
    elif live:
        r = (2, 'neutral', f"반등 신호 뒤 {s['age']}거래일 · 새 신호 없음", '급락 뒤 반등이 이어지는 중입니다(새 신호는 없음)')
    else:
        r = (0, 'neutral', '뚜렷한 신호 없음', '뚜렷한 신호가 없습니다')
    lines = []
    last, past = d.get('last'), d.get('past')
    if d.get('on') and past:
        lines.append(f"과거 노란 점 {past['n']}번: 다음 날 샀다면 {past['hit']}%가 {past['days']}거래일 안에 +{TARGET}% · "
                     f"가는 길에 평균 {past['maeAvg']}% 더 빠짐")
    elif last and last.get('pmv') is not None:
        lines.append(f"노란 점({md_(last['date'])}) 뒤 {t['prod']} <b>{last['pmv']:+.1f}%</b>")
    if live:
        lines.append(f"취소선 {t['base']} ${s['low']} · 지금보다 {s['dist']:.1f}% 아래")
    elif t.get('setupUp'):
        lines.append(f"{t['setupUp']['left']}거래일 안에 반등 신호가 나와야 합니다")
    e = t.get('easy') or {}
    if e.get('head') == '어제와 판단이 달라짐' and e.get('why'):
        lines.append('어제와 달라짐: ' + e['why'][0])
    return {'i': r[0], 'cls': r[1], 'text': r[2], 'say': r[3], 'lines': lines}


def headline(cards, vx):
    """두 종목을 합친 결론 한 문장(+ 다만 …). 같은 단계면 '모두'로 묶는다"""
    groups = {}
    for c in cards:
        g = c.get('stage') or stage_of(c)
        groups.setdefault((g['i'], g['cls']), (g['say'], []))[1].append(c['prod'])
    out = []
    for say, prods in groups.values():
        if len(prods) > 1:
            subj = '·'.join(NAMES.get(p, (p, '는'))[0] for p in prods) + ' 모두'
        else:
            nm, j = NAMES.get(prods[0], (prods[0], '는')); subj = nm + j
        out.append(f'{subj} {say}.')
    # '다만 …'은 한 문장으로 잇는다 (앞 것은 '…고', 마지막은 '…니다')
    live = [c for c in cards if c['state'] != 'wait']
    rebound = [c for c in live if (c.get('stage') or {}).get('i') == 2]
    dates = sorted({c['dip']['last']['date'] for c in rebound
                    if c.get('dip') and c['dip'].get('last') and not c['dip']['on']})
    but = []
    if dates:
        d = ', '.join(md_(x) for x in dates)
        but.append((f'가장 싸게 살 수 있던 자리({d})는 지났고', f'가장 싸게 살 수 있던 자리({d})는 지났습니다'))
    if live and all(c.get('weak') for c in live) and any((c.get('stage') or {}).get('i') in (1, 2) for c in live):
        but.append(('큰 흐름(주·월 단위)은 아직 약세이고', '큰 흐름(주·월 단위)은 아직 약세입니다'))
    if vx and vx['st'] == 'worse' and not any(c['state'] == 'warn' for c in live):
        but.append(('시장 불안(VIX)이 커지는 중이고', '시장 불안(VIX)이 커지는 중입니다'))
    if but:
        out.append('다만 ' + ', '.join([x[0] for x in but[:-1]] + [but[-1][1]]) + '.')
    return ' '.join(out)


def main():
    bars = {}
    for s in SYMS:
        bars[s] = fetch(s)          # 장이 아직 안 끝난 오늘 봉은 빠져 있다
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
            t['easy'] = plain_reason(y, t)
            t['dip'] = dip_card(t, base, prod, bars)
            t['easyTitle'], t['easySub'] = easy_state(t)
            t['easyRisks'] = plain_risks(t, vxf)
            t['risks'] = risks(t, vxf)
            t['next'] = next_states(base, t, vx)
            t['hist'] = history(base, prod, bars, t)
            t['stage'] = stage_of(t)
        cards.append(t)
        print(f"{prod}({base}): {t['label']} | {t.get('change', '')}")

    score = {}; events = {}; table = []
    inv = {}
    for c in cards:
        sg = c.get('sig')
        if sg and sg['valid'] and sg['age'] <= 60:
            inv[c['base']] = f"${sg['low']} (지금보다 {sg['dist']}% 아래)"
            if 'pLow' in sg:
                inv[c['prod']] = f"취소될 때 약 ${sg['pLow']} (지금보다 {sg['pDist']}% 아래)"
    for prod, base in PAIRS:
        if bars[prod]:
            rows, sigs, divs, B, P, pidx = backtest(base, prod, bars)
            score[prod] = {'from': P[0]['d'], 'rows': scorecard(rows)}
            card = next(c for c in cards if c['prod'] == prod)
            if card['state'] != 'wait':
                card['past'] = past_like(card, rows)
            events[prod] = event_log(base, prod, rows, sigs, divs, B, P, pidx)
    for s in ('SOXX', 'SOXL', 'QQQ', 'TQQQ', 'UVIX', 'VXX', 'SPY', '^VIX'):
        table.append(summary_row(s.replace('^', ''), bars[s], inv.get(s)))

    out = {'rule': RULE, 'asof': asof, 'cost': COST, 'target': TARGET,
           'table': table, 'score': score, 'events': events,
           'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='minutes'),
           'cards': cards,
           'uvix': uvix_card(bars['UVIX'], bars['VXX']) if len(bars['UVIX']) > 60 else None,
           'market': market_card(bars['SPY'], bars['^VIX'], vx, vxf),
           'headline': headline(cards, vx)}
    with open('desk.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    save_log(out)
    print('desk.json 저장 —', asof)


if __name__ == '__main__':
    main()
