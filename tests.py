#!/usr/bin/env python3
"""계산 시험 — `python3 tests.py`. 인터넷 없이 돈다.

지표(%K·CCI·RSI), 노란 점, 회복 여부, SCR 신호, VIX 상태, etfs.json 형식을 확인한다.
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
        I = ind([20, 21, 20, None], [-100, -150, -99, -150], [30, 25, 25, 25])
        self.assertTrue(market.is_dip(I, 0))      # 기준선과 같으면 켜짐
        self.assertFalse(market.is_dip(I, 1))     # %K 21
        self.assertFalse(market.is_dip(I, 2))     # CCI −99
        self.assertFalse(market.is_dip(I, 3))     # 값이 없으면 꺼짐

    def test_dip_past_counts_runs_once(self):
        # 노란 점이 사흘 연달아 켜지면 한 번으로 센다. 그다음 날 시가에 사서 +10%에 닿으면 도달
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
        # 사고 나서 계속 빠지면 못 닿음, 60거래일 뒤 종가 기준 손실을 센다
        n = 80
        I = ind([10 if i == 5 else 50 for i in range(n)], [-150 if i == 5 else 0 for i in range(n)],
                [20 if i == 5 else 50 for i in range(n)])
        c = [100.0] * 7 + [100.0 - 0.5 * i for i in range(n - 7)]
        B = bars(c, spread=0.1)
        p = market.dip_past(B, I, B)
        self.assertEqual((p['n'], p['hit'], p['miss']), (1, 0, 1))
        self.assertLess(p['missAvg'], -20)

    def test_dip_past_skips_unfinished(self):
        # 기간(60거래일)이 아직 안 끝났고 목표에도 못 닿은 최근 건은 세지 않는다
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


class ScrSignals(unittest.TestCase):
    def test_strict_recovery(self):
        # 전날 세 지표가 모두 과매도, 오늘 모두 기준선 위 → 엄격 동시 회복
        I = ind([50, 10, 30], [0, -150, -50], [50, 25, 35])
        sigs, setup = desk.scr_signals(bars([100, 90, 92]), I)
        self.assertEqual([(s['side'], s['i'], s['strict']) for s in sigs], [('up', 2, True)])
        self.assertEqual(sigs[0]['ext'], 90)     # 관찰 저점 = 준비~회복 구간의 최저 종가
        self.assertIsNone(setup['up'])

    def test_sequential_recovery(self):
        # 세 지표가 며칠에 걸쳐 차례로 올라오면 순차 회복
        I = ind([10, 30, 30, 30], [-150, -150, -50, -50], [25, 25, 25, 35])
        sigs, _ = desk.scr_signals(bars([100, 95, 96, 97]), I)
        self.assertEqual([(s['i'], s['strict']) for s in sigs], [(3, False)])

    def test_setup_expires(self):
        # 준비 뒤 10거래일 안에 회복하지 못하면 만료
        n = 14
        I = ind([10] + [30] * (n - 1), [-150] * n, [25] * n)
        sigs, setup = desk.scr_signals(bars([100.0] * n), I)
        self.assertEqual(sigs, [])
        self.assertIsNone(setup['up'])


class Vix(unittest.TestCase):
    def V(self, closes):
        return [{'d': str(i), 'o': c, 'h': c, 'l': c, 'c': c} for i, c in enumerate(closes)]

    def test_spike_is_worse(self):
        self.assertEqual(desk.vix_state(self.V([20] * 8 + [22.1]))['st'], 'worse')     # 하루 +10.5%

    def test_two_up_above_avg_is_worse(self):
        self.assertEqual(desk.vix_state(self.V([20] * 6 + [21, 22]))['st'], 'worse')

    def test_two_down_below_avg_is_confirm(self):
        self.assertEqual(desk.vix_state(self.V([20] * 5 + [25, 19, 18]))['st'], 'confirm')

    def test_flat_is_neutral(self):
        self.assertEqual(desk.vix_state(self.V([20] * 9))['st'], 'neutral')


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
            if 'base' in e:
                self.assertIn(e['mult'], (2, 3))
        for t, x in j.get('extra', {}).items():
            self.assertNotIn(t, tks)
            self.assertTrue(x['base'] and x['mult'])
        self.assertIn('TQQQ', tks)
        self.assertEqual([e['tk'] for e in j['etfs'] if not e.get('base')], ['AIBU'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
