#!/usr/bin/env python3
"""계산 시험 — `python3 tests.py`. 인터넷 없이 돈다.

지표(%K·CCI·RSI), 옐로우닷(예전 옐로우닷), 회복 여부, 옐로우업다운, 상황판 상태·결론 문장, etfs.json 형식을 확인한다.
GitHub에서는 .py·etfs.json·test_data.json이 바뀔 때마다 자동으로 돈다(.github/workflows/test.yml).
"""
import json, unittest
import market, desk


def bars(closes, spread=1.0):
    """종가만으로 시험용 일봉을 만든다 (고가 = 종가 + spread, 저가 = 종가 − spread, 시가 = 종가)"""
    return [{'d': f'2026-01-{i + 1:02d}' if i < 31 else f'2026-{2 + (i - 31) // 28:02d}-{(i - 31) % 28 + 1:02d}',
             'o': c, 'h': c + spread, 'l': c - spread, 'c': c} for i, c in enumerate(closes)]


def ind(k, cci, rsi):
    """세 지표 값을 직접 넣은 시험용 지표 묶음"""
    n = len(k)
    return {'k': k, 'cci': cci, 'rsi': rsi, 'bl': [None] * n, 'disp': [None] * n}


class Indicators(unittest.TestCase):
    def test_rsi_textbook(self):
        # Wilder RSI 교과서 예시(StockCharts). 교과서 표는 중간값을 반올림해서 0.1 안쪽 차이가 난다
        c = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
             46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
        want = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]
        got = market.rsi_w(c)[14:]
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=0.1)
        self.assertIsNone(market.rsi_w(c)[13])

    def test_cci_simple(self):
        # 전형가격 1·2·3이면 평균 2, 평균절대편차 2/3 → (3−2)/(0.015×2/3) = 100
        B = [{'h': x, 'l': x, 'c': x} for x in (1.0, 2.0, 3.0)]
        self.assertAlmostEqual(market.cci(B, 3)[-1], 100.0)
        self.assertEqual(market.cci([{'h': 5, 'l': 5, 'c': 5}] * 3, 3)[-1], 0.0)   # 움직임이 없으면 0

    def test_stoch_simple(self):
        B = [{'h': 10, 'l': 0, 'c': 5}] * 4 + [{'h': 10, 'l': 0, 'c': 7.5}]
        self.assertAlmostEqual(market.stoch_k(B)[-1], 75.0)
        self.assertEqual(market.stoch_k([{'h': 3, 'l': 3, 'c': 3}] * 5)[-1], 50.0)  # 범위가 0이면 50

    def test_verified_values(self):
        # 세라핀이 가격 자료로 따로 다시 계산한 2026-11-10 값과 맞는지 (재계산 일치)
        with open('test_data.json', encoding='utf-8') as f:
            fx = json.load(f)
        want = {'SOXX': (61.7458, -91.2000, 39.1054), 'QQQ': (50.9375, -96.2323, 40.1241)}
        for sym, (k, cci, rsi) in want.items():
            B = [{'d': d, 'h': h, 'l': l, 'c': c} for d, h, l, c in fx[sym]]
            self.assertEqual(B[-1]['d'], '2026-11-10')
            I = market.indicators(B)
            self.assertAlmostEqual(I['k'][-1], k, delta=0.05, msg=sym + ' %K')
            self.assertAlmostEqual(I['cci'][-1], cci, delta=0.05, msg=sym + ' CCI')
            self.assertAlmostEqual(I['rsi'][-1], rsi, delta=0.05, msg=sym + ' RSI')


class YellowDot(unittest.TestCase):
    def test_is_dip(self):
        I = ind([20, 21, 20, None, 10], [-100, -150, -150, -150, 50], [30, 25, 31, 25, 20])
        self.assertTrue(market.is_dip(I, 0))      # 기준선과 같으면 켜짐
        self.assertFalse(market.is_dip(I, 1))     # %K 21
        self.assertFalse(market.is_dip(I, 2))     # RSI 31
        self.assertFalse(market.is_dip(I, 3))     # 값이 없으면 꺼짐
        self.assertTrue(market.is_dip(I, 4))      # v3.1부터 CCI는 보지 않는다 (2026-11-24)

    def test_dip_past_counts_runs_once(self):
        # 옐로우닷이 사흘 연달아 켜지면 한 번으로 센다. 그다음 날 시가에 사서 +10%에 닿으면 도달
        n = 80
        on = [5, 6, 7]
        I = ind([10 if i in on else 50 for i in range(n)], [-150 if i in on else 0 for i in range(n)],
                [20 if i in on else 50 for i in range(n)])
        c = [100] * 8 + [100 + 2 * i for i in range(n - 8)]
        B = bars(c, spread=0.5)
        p = market.dip_past(B, I, B)
        self.assertEqual(p['n'], 1)
        self.assertEqual(p['hit'], 100)
        self.assertEqual(p['miss'], 0)

    def test_dip_past_miss_loss(self):
        # 사고 나서 계속 빠지면 못 닿음, 긴 기간(DIP_DAYS · 30거래일) 뒤 종가 기준 손실을 센다
        n = 80
        I = ind([10 if i == 5 else 50 for i in range(n)], [-150 if i == 5 else 0 for i in range(n)],
                [20 if i == 5 else 50 for i in range(n)])
        c = [100.0] * 7 + [100.0 - 0.5 * i for i in range(n - 7)]
        B = bars(c, spread=0.1)
        p = market.dip_past(B, I, B)
        self.assertEqual((p['n'], p['hit'], p['miss']), (1, 0, 1))
        self.assertLess(p['missAvg'], -10)                         # 하루 0.5씩 30거래일 → 약 −14%

    def test_dip_past_skips_unfinished(self):
        # 기간(30거래일)이 아직 안 끝났고 목표에도 못 닿은 최근 건은 세지 않는다
        n = 30
        I = ind([10 if i == 5 else 50 for i in range(n)], [-150 if i == 5 else 0 for i in range(n)],
                [20 if i == 5 else 50 for i in range(n)])
        B = bars([100.0] * n, spread=0.1)
        self.assertIsNone(market.dip_past(B, I, B))


class Recovery(unittest.TestCase):
    def test_levels(self):
        long = [50.0] * 700 + [100.0] * 60
        self.assertEqual(market.recovery(bars(long + [95]))['v'], '회복')
        self.assertEqual(market.recovery(bars(long + [70]))['v'], '부분회복')
        self.assertEqual(market.recovery(bars(long + [50]))['v'], '미회복')
        # 경계: −10%까지 회복, −40%까지 부분회복
        self.assertEqual(market.recovery(bars(long + [89]))['v'], '부분회복')
        self.assertEqual(market.recovery(bars(long + [60]))['v'], '부분회복')
        self.assertEqual(market.recovery(bars(long + [59]))['v'], '미회복')
        self.assertEqual(market.recovery(bars([100.0] * 100 + [95]))['v'], '이력부족')
        r = market.recovery(bars(long + [90]), 'IWM')
        self.assertEqual((r['v'], r['dd'], r['by']), ('회복', -10.0, 'IWM'))
        self.assertIsNone(market.recovery([]))


class FillLast(unittest.TestCase):
    """야후 일봉이 아직 안 올라왔을 때 시세 요약(meta)으로 마지막 하루만 채운다"""
    def setUp(self):
        self._io = market._intraday_open
        market._intraday_open = lambda sym, day: 33.88
        self.res = {'meta': {'regularMarketTime': 1794862800,          # 2026-11-16 16:00 뉴욕(월)
                             'regularMarketPrice': 32.48, 'regularMarketDayHigh': 34.2,
                             'regularMarketDayLow': 32.11, 'regularMarketVolume': 945455}}

    def tearDown(self):
        market._intraday_open = self._io

    def bar(self, d):
        return [{'d': d, 'o': 33, 'h': 33.5, 'l': 32.5, 'c': 33.05, 'v': 1, 'a': 33.05}]

    def test_fills_next_session(self):
        out = market._fill_last('NAIL', self.res, self.bar('2026-11-13'))   # 금 → 월, 사이 평일 없음
        self.assertEqual(len(out), 2)
        self.assertEqual({k: out[-1][k] for k in 'dochl'},
                         {'d': '2026-11-16', 'o': 33.88, 'c': 32.48, 'h': 34.2, 'l': 32.11})

    def test_skips_when_a_day_is_missing(self):
        out = market._fill_last('NAIL', self.res, self.bar('2026-11-12'))   # 금요일이 빠져 있음
        self.assertEqual(len(out), 1)

    def test_no_change_when_daily_is_there(self):
        out = market._fill_last('NAIL', self.res, self.bar('2026-11-16'))
        self.assertEqual(len(out), 1)


class DipTargets(unittest.TestCase):
    """옐로우닷 다음 날 시가에 샀다면 +10% 목표가와 처음 닿은 날"""
    def test_hit_day(self):
        B = bars([100] * 10)
        I = ind([50, 50, 10, 50, 50, 50, 50, 50, 50, 50], [0, 0, -150] + [0] * 7, [50, 50, 20] + [50] * 7)
        P = bars([10, 10, 10, 10, 10.5, 10.8, 11.2, 11, 11, 11], spread=0.1)   # 다음 날(3번) 시가 10 → 목표 11
        t = market.dip_targets(B, I, P, B[0]['d'])
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]['d0'], t[0]['buy'], t[0]['tgt']), (B[2]['d'], P[3]['d'], 11.0))
        self.assertEqual(t[0]['hit'], P[6]['d'])          # 5번 고가 10.9 < 11, 6번 고가 11.3 ≥ 11
        self.assertFalse(t[0]['open'])


class DipPast20(unittest.TestCase):
    """과거 결과의 +20%: 닿은 비율·걸린 날. 기간이 안 끝났고 아직 못 닿은 건은 +20% 비율에서 뺀다"""
    I = ind([50, 50, 10] + [50] * 7, [0, 0, -150] + [0] * 7, [50, 50, 20] + [50] * 7)

    def test_both_targets(self):
        P = bars([10, 10, 10, 10, 10.5, 11.2, 12.2, 12, 12, 12], spread=0.1)   # 산 값 10 → 5번 +10%, 6번 +20%
        p = market.dip_past(bars([100] * 10), self.I, P)
        self.assertEqual((p['hit'], p['medD'], p['hit20'], p['medD20'], p['n20']), (100, 3, 100, 4, 1))   # 옐로우닷 날부터 센 거래일

    def test_pending_second_target_left_out(self):
        P = bars([10, 10, 10, 10, 10.5, 11.2, 11.2, 11, 11, 11], spread=0.1)   # +10%만 닿았고 기간은 진행 중
        p = market.dip_past(bars([100] * 10), self.I, P)
        self.assertEqual((p['hit'], p['hit20'], p['n20']), (100, None, 0))

    def test_real_data_invariant(self):
        with open('test_data.json', encoding='utf-8') as f:
            fx = json.load(f)
        B = [{'d': d, 'o': c, 'h': h, 'l': l, 'c': c} for d, h, l, c in fx['SOXX']]
        p = market.dip_past(B, market.indicators(B), B)
        self.assertLessEqual(p['hit20'], p['hit'])
        self.assertLessEqual(p['n20'], p['n'])


class DipPastFast(unittest.TestCase):
    """15거래일 안 +10%(hitF): 15일이 지났으면 30일이 안 끝났어도 세고, 16일째 넘어 닿은 건은 15일 기준으로는 못 닿음"""
    def run_case(self, closes):
        n = len(closes)
        I = ind([10] + [50] * (n - 1), [-150] + [0] * (n - 1), [20] + [50] * (n - 1))   # 0번 날만 옐로우닷
        return market.dip_past(bars([100] * n), I, bars(closes, spread=0.1))

    def test_late_hit_counts_only_for_long(self):
        p = self.run_case([10] * 22 + [11.5] + [11] * 50)           # 산 값 10, 22번(옐로우닷 뒤 22거래일)에 +10% — 30일 안 · 15일 밖
        self.assertEqual((p['hit'], p['hitF'], p['nF'], p['missF']), (100, 0, 1, 1))
        self.assertEqual(p['missAvgF'], 0.0)                         # 15거래일 뒤 종가 10 → 0%

    def test_counted_before_long_end(self):
        p = self.run_case([10] * 30)                                 # 30일은 안 끝남, 15일은 끝남 · 못 닿음
        self.assertIsNone(p)                                         # 30일 결과가 없으면 통째로 없음
        p = self.run_case([10] * 5 + [11.5] + [10] * 10)             # 5번째 날 닿음 — 기간이 짧아도 셈
        self.assertEqual((p['hit'], p['hitF'], p['nF']), (100, 100, 1))

    def test_real_data_invariant(self):
        with open('test_data.json', encoding='utf-8') as f:
            fx = json.load(f)
        B = [{'d': d, 'o': c, 'h': h, 'l': l, 'c': c} for d, h, l, c in fx['SOXX']]
        p = market.dip_past(B, market.indicators(B), B)
        self.assertGreaterEqual(p['nF'], p['n'])                     # 15일은 30일보다 먼저 끝나므로 표본이 같거나 많다
        self.assertTrue(0 <= p['hitF'] <= 100)


class UpDown(unittest.TestCase):
    """옐로우업다운(옐로우닷 시작): 매수 LP×(1−5%−n×0.2%) · 매도 LP×(1+10%−n×0.2%), 돈이 바닥나면 평단 +5%에 전부 매도, 돈이 마이너스가 되지 않음"""
    def P(self, closes):
        return [{'d': f'2026-01-{i + 1:02d}', 'o': c, 'h': c, 'l': c, 'c': c} for i, c in enumerate(closes)]

    def test_buy_then_sell_out(self):
        # 1번 10에 1포트 → 2번 9.4(≤ 10×0.948=9.48) 매수, 2포트 → 3번 10.4(≥ 9.4×1.096=10.30) 매도 → 4번 11.5(≥ 10.4×1.098) 매도, 끝
        x = market.updown_cycle(self.P([10, 10, 9.4, 10.4, 11.5]), 1)
        self.assertTrue(x['done']); self.assertEqual((x['days'], x['peak']), (3, 2))
        self.assertGreater(x['ret'], 0)

    def test_no_trade_inside_band(self):
        # 9.5는 매수가 9.48보다 높고, 10.9는 매도가 10×1.098=10.98보다 낮다 — 아무것도 안 하고 도는 중
        x = market.updown_cycle(self.P([10, 10, 9.5, 10.9]), 1)
        self.assertEqual(x['run']['n'], 1)
        self.assertEqual((x['run']['buy'], x['run']['sell']), (9.48, 10.98))

    def test_exhausted_never_negative(self):
        # 계속 7%씩 빠지면 10포트를 다 쓰고 매도만 남는다. 돈은 0 아래로 가지 않는다
        c = [10, 10]
        for _ in range(40):
            c.append(round(c[-1] * 0.93, 4))
        x = market.updown_cycle(self.P(c), 1)
        self.assertTrue(x['run']['out'])
        self.assertIsNone(x['run']['buy'])
        self.assertGreater(x['run']['ret'], -100)

    def test_exhausted_then_even_exit(self):
        # 7%씩 빠지며 10포트를 다 쓴 뒤(돈이 바닥남): 하루 +9% 올라도 평단 +5% 밑이면 팔지 않고, 평단 +5%를 넘는 날 전부 판다
        c = [10, 10]
        for _ in range(9):
            c.append(round(c[-1] * 0.93, 4))
        x = market.updown_cycle(self.P(c + [round(c[-1] * 1.09, 4)]), 1)
        self.assertTrue(x['run']['out']); self.assertEqual(x['run']['n'], 10)          # 나눠 팔지 않음
        self.assertAlmostEqual(x['run']['sell'], x['run']['avg'] * 1.05, delta=0.01)
        y = market.updown_cycle(self.P(c + [round(x['run']['sell'] * 1.001, 4)]), 1)
        self.assertTrue(y['done']); self.assertEqual(y['peak'], 10)
        self.assertGreater(y['ret'], 3)                                               # 평단 +5% − 비용

    def test_skip_overlap_and_pending(self):
        with open('test_data.json', encoding='utf-8') as f:
            fx = json.load(f)
        B = [{'d': d, 'o': c, 'h': h, 'l': l, 'c': c} for d, h, l, c in fx['SOXX']]
        u = market.updown_past(B, market.indicators(B), B)
        self.assertTrue(0 <= u['win'] <= 100 and 1 <= u['used'] <= market.UD_POTS)
        starts = sum(1 for i in range(len(B)) if market.is_dip(market.indicators(B), i))
        self.assertLessEqual(u['n'], starts)


class UvixDip(unittest.TestCase):
    """UVIX 옐로우닷(참고용): 아무 날 기준선, 그 뒤 60거래일 주식(같은 기간 아무 날과 나란히 · desk.UVIX_DAYS)"""
    def test_shape(self):
        with open('test_data.json', encoding='utf-8') as f:
            fx = json.load(f)
        B = [{'d': d, 'o': c, 'h': h, 'l': l, 'c': c} for d, h, l, c in fx['SOXX']]
        x = desk.uvix_dip(B, {'SPY': B, 'QQQ': B, 'SOXX': B})
        self.assertEqual([r['s'] for r in x['stocks']], ['SPY', 'QQQ', 'SOXX'])
        r = x['stocks'][0]
        self.assertLessEqual(r['dip']['n'], x['past']['n'] + 1)
        self.assertGreater(r['all']['n'], r['dip']['n'])
        self.assertTrue(0 <= x['any']['hit20'] <= x['any']['hit'] <= 100)


class UpToDate(unittest.TestCase):
    """예약 실행 세 번 중 이미 받은 날은 뒤의 것을 건너뛴다"""
    def check(self, dates, now):
        import tempfile, os, prices, datetime as dt
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'data': {f'T{i}': {'date': d} for i, d in enumerate(dates)}}, f)
        try:
            return prices.up_to_date(f.name, dt.datetime.fromisoformat(now))
        finally:
            os.unlink(f.name)

    def test_after_monday_close(self):
        self.assertFalse(self.check(['2026-11-13'] * 3, '2026-11-16T22:23+00:00'))   # 월요일 첫 시도
        self.assertTrue(self.check(['2026-11-16'] * 3, '2026-11-17T01:47+00:00'))    # 두 번째는 건너뜀
        self.assertFalse(self.check(['2026-11-16', '2026-11-13'], '2026-11-17T01:47+00:00'))  # 한 종목이라도 늦으면 다시

    def test_weekend_uses_friday(self):
        self.assertTrue(self.check(['2026-11-20'], '2026-11-21T05:17+00:00'))        # 토요일 → 금요일 종가

    def test_ny_time_and_dst(self):
        # 예약 수집은 뉴욕 16:16 — 겨울(EST)은 21:16 UTC, 서머타임(EDT)은 20:16 UTC
        self.assertFalse(self.check(['2026-11-19'], '2026-11-20T21:16+00:00'))       # 겨울 16:16 → 그날 종가를 받아야 함
        self.assertTrue(self.check(['2026-11-19'], '2026-11-20T21:10+00:00'))        # 16:10은 아직 전날 기준
        self.assertFalse(self.check(['2026-07-14'], '2026-07-15T20:16+00:00'))       # 서머타임 16:16 → 받아야 함
        self.assertTrue(self.check(['2026-07-14'], '2026-07-15T20:10+00:00'))


class EtfList(unittest.TestCase):
    def test_etfs_json(self):
        with open('etfs.json', encoding='utf-8') as f:
            j = json.load(f)
        need = {'tk', 'name', 'lev', 'lq', 'ref', 'up', 'dn', 'px', 'n1', 'sig', 'sigEst', 'mdd', 'mddEst',
                'corr', 'warns', 'type', 'desc', 'scen'}
        tks = [e['tk'] for e in j['etfs']]
        self.assertEqual(len(tks), len(set(tks)), '티커가 겹침')
        for e in j['etfs']:
            self.assertTrue(need <= set(e), e['tk'] + ' 빠진 칸: ' + ', '.join(need - set(e)))
            self.assertIsInstance(e['warns'], list)
            self.assertIsInstance(e['scen'], list)
            self.assertNotIn('미회복', e['warns'], '회복 여부는 손으로 적지 않는다')
            for w in ('유동성 낮음', '이력 부족'):
                self.assertNotIn(w, e['warns'], w + '은 거래대금·거래일 수로 계산한다')
            if 'base' in e:
                self.assertIn(e['mult'], (2, 3))
        for t, x in j.get('extra', {}).items():
            self.assertNotIn(t, tks)
            self.assertTrue(x['base'] and x['mult'])
        self.assertIn('TQQQ', tks)
        self.assertEqual([e['tk'] for e in j['etfs'] if not e.get('base')], ['AIBU'])


class DeskLive(unittest.TestCase):
    """장중 지금 가격 — desk.live_state에 오늘 봉만 더한 셈(화면 lvCalc와 같은 식)이 market.indicators와 같다"""
    def test_live_state_matches_indicators(self):
        import math
        B = [{'d': f'2026-01-{i:02d}', 'o': 0, 'h': 100 + 10 * math.sin(i / 3) + 2, 'l': 100 + 10 * math.sin(i / 3) - 2,
              'c': 100 + 10 * math.sin(i / 3) + math.cos(i)} for i in range(1, 80)]
        I = market.indicators(B)
        s = desk.live_state(B[:-1]); b = B[-1]
        hh = max(s['h4'] + [b['h']]); ll = min(s['l4'] + [b['l']]); k = (b['c'] - ll) / (hh - ll) * 100
        d = b['c'] - s['pc']; g = (s['g'] * 13 + max(d, 0)) / 14; lo = (s['l'] * 13 + max(-d, 0)) / 14
        rsi = 100 - 100 / (1 + g / lo)
        self.assertAlmostEqual(k, I['k'][-1], places=3)
        self.assertAlmostEqual(rsi, I['rsi'][-1], places=3)
        self.assertEqual((s['d'], len(s['h4'])), (B[-2]['d'], 4))
        self.assertNotIn('tp19', s)   # v3.1: CCI를 빼서 19일 기준가는 보내지 않는다


class MonthWarn(unittest.TestCase):
    """월봉 과열 꺾임 — 월봉 %K·CCI·RSI가 모두 과열권에 들어갔다가 모두 내려온 달 (desk.month_warn, 시장 참고)"""
    def bars(self, closes, start=(2000, 1)):
        out = []; y, m = start
        for c in closes:
            out.append({'d': f'{y}-{m:02d}-15', 'o': c, 'h': c * 1.01, 'l': c * 0.99, 'c': c, 'a': c})
            m += 1
            if m > 12: y += 1; m = 1
        return out

    def test_exit_after_overbought(self):
        up = [100 * 1.03 ** i for i in range(30)]              # 30달 꾸준히 오름 → 과열권
        down = [up[-1] * 0.9, up[-1] * 0.8, up[-1] * 0.75]     # 크게 빠짐 → 셋 다 내려옴
        flat = [up[-1] * 0.75] * 14
        w = desk.month_warn('X', self.bars(up + down + flat))
        self.assertIsNotNone(w)
        self.assertEqual(w['n'], 1)
        self.assertIn(w['last']['m'], ('2002-07', '2002-08', '2002-09'))
        self.assertFalse(w['last']['open'])                    # 날짜가 15일이라 그 달은 끝난 것으로 셈(마지막 달 빼고)

    def test_month_done(self):
        self.assertTrue(desk.month_done('2026-11-30'))         # 월요일 · 그 달 마지막 평일
        self.assertFalse(desk.month_done('2026-11-23'))
        self.assertTrue(desk.month_done('2026-10-30'))         # 금요일 뒤 주말 → 11월


class DeskStatus(unittest.TestCase):
    """상황판 v3.0 — 옐로우닷 상태 줄과 결론 문장 (원문 판정은 없다 · 옐로우업다운 매수·매도도 쓰지 않는다)"""
    def card(self, base='SOXX', prod='SOXL', on=False, run=1, last=None, past=None):
        d = {'on': on, 'run': run}
        if last: d['last'] = last
        if past: d['past'] = past
        c = {'base': base, 'prod': prod, 'state': 'ok', 'dip': d}
        c['stage'] = desk.stage_of(c)
        return c

    def test_on_shows_past_results(self):
        past = {'n': 26, 'hit': 92, 'hitF': 76}
        g = self.card(on=True, past=past)['stage']
        self.assertEqual((g['cls'], g['text']), ('on', '옐로우닷 오늘'))
        self.assertEqual(g['stats'], [('과거', [76, 92], 'past')])          # [15거래일 안 +10%, 30거래일 안 +10%] 색 칸
        self.assertNotIn('ud', g)
        self.assertEqual(self.card(on=True, run=3)['stage']['text'], '옐로우닷 3일째')

    def test_off(self):
        g = self.card(last={'date': '2026-10-14', 'age': 27, 'pmv': -17.8})['stage']
        self.assertEqual((g['cls'], g['text']), ('off', '옐로우닷 꺼짐'))
        self.assertEqual(g['stats'], [('옐로우닷(10/14) 뒤', '-17.8%', 'dot')])
        self.assertEqual(desk.stage_of({'state': 'wait'})['cls'], 'wait')

    def test_headline(self):
        cs = [self.card('SPY', 'UPRO', on=True), self.card('QQQ', 'TQQQ', on=True), self.card(on=True, run=3)]
        self.assertEqual(desk.headline(cs), 'S&P500·나스닥·반도체 모두 옐로우닷이 켜졌습니다.')
        cs = [self.card('SPY', 'UPRO', on=True), self.card('QQQ', 'TQQQ'), self.card()]
        self.assertEqual(desk.headline(cs), 'S&P500 옐로우닷이 켜졌습니다.')
        cs = [self.card('SPY', 'UPRO', last={'date': '2026-10-14', 'age': 5, 'pmv': 1}), self.card('QQQ', 'TQQQ'), self.card()]
        h = desk.headline(cs)
        self.assertEqual(h, '옐로우닷이 모두 꺼져 있습니다. 마지막은 SPY 10/14입니다.')
        self.assertNotIn('반등', h)                                          # 원문(블로그) 말은 쓰지 않는다
        self.assertNotIn('옐로우업다운', h)                                  # 상황판은 그날의 상황만 (2026-11-23 사용자)


if __name__ == '__main__':
    unittest.main(verbosity=2)
