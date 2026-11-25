"""프리장 시세 — 뉴욕 평일 4:00~9:30 동안 Webull 공개 시세를 받아 pre.json에 둔다.

2026-11-25 사용자: 폰(한국)에서 Webull 주소가 막혀(연결이 안 됨) 장중 버튼이 프리장 가격을 못 받는다 — '니가 받아서 내 쪽에 넣어 줘'.
GitHub 서버(미국)에서 받아 사이트에 올려 두면 화면이 그 파일을 읽는다(같은 사이트라 막힐 일이 없다).
화면은 Webull을 직접 받아 보고, 안 되면 이 파일(오늘 것이고 30분 안)을 쓴다.

  python3 pre.py          한 번 받아 pre.json 쓰기
  python3 pre.py --loop   9:31까지 10분마다 받아 커밋 · 푸시 (워크플로 pre.yml, Claude 예약 작업이 뉴욕 4:04에 시작)

pre.json: {"at": 받은 시각, "q": {기호: {"c": 프리장 가격, "h", "l"(= c), "pc": 전날 종가, "t": 마지막 거래 시각}}}
— 오늘 프리장 거래가 있는 기호만. 화면 lvQuote가 그대로 읽는 Finnhub 모양이다.
종목 번호(tickerId)는 wbid.json — data.json · desk.json에 새 기호가 생기면 여기서 찾아 더한다.
공식 API가 아니라 Webull 앱 · 사이트가 쓰는 주소라 언제든 막힐 수 있다(그러면 화면은 Alpaca 무료 키로)."""
import json, sys, time, subprocess, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
HOSTS = ['https://quotes-gw.webullfintech.com/api', 'https://quotes-gw.webullbroker.com/api']
EVERY = 600            # 10분 — GitHub Pages는 1시간에 10번쯤까지 다시 올린다
LIMIT = 5.8 * 3600     # 워크플로 한 번은 6시간까지


def ny_now():
    return datetime.now(NY)


def in_pre(t):
    hm = t.hour * 100 + t.minute
    return t.weekday() < 5 and 400 <= hm < 930


def get(path):
    err = None
    for h in HOSTS:
        try:
            r = urllib.request.Request(h + path, headers={'User-Agent': 'Mozilla/5.0'})
            return json.load(urllib.request.urlopen(r, timeout=20))
        except Exception as e:
            err = e
    print('Webull 받지 못함:', err)
    return None


def ts(s):
    for f in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z'):
        try:
            return datetime.strptime(s, f).timestamp()
        except (ValueError, TypeError):
            pass
    return 0


def quote(x):
    """Webull 시세 하나 → 오늘 프리장 거래면 {c,h,l,pc,t}, 아니면 None (화면 wbQuote의 프리장 쪽과 같은 셈)"""
    try:
        c, pp = float(x['close']), float(x.get('pPrice') or 0)
    except (KeyError, TypeError, ValueError):
        return None
    tt, mt = ts(x.get('tradeTime')), ts(x.get('mkTradeTime'))
    if not (c > 0 and pp > 0 and tt and mt):
        return None
    a, b = datetime.fromtimestamp(tt, NY), datetime.fromtimestamp(mt, NY)
    if a.date() > b.date() and a.hour * 100 + a.minute < 930:
        return {'c': pp, 'h': pp, 'l': pp, 'pc': c, 't': int(tt)}
    return None


def symbols():
    out = []
    try:
        for c in json.load(open('desk.json')).get('cards', []):
            if c.get('base') and c['base'] not in out:
                out.append(c['base'])
    except (OSError, ValueError):
        pass
    for tk, L in json.load(open('data.json'))['data'].items():
        for s in (tk, (L.get('dip') or {}).get('base')):
            if s and s not in out:
                out.append(s)
    return out


def ids():
    """wbid.json — 없는 기호는 찾아서 더한다"""
    try:
        W = json.load(open('wbid.json'))
    except (OSError, ValueError):
        W = {}
    new = False
    for s in symbols():
        if s in W:
            continue
        j = get('/search/pc/tickers?' + urllib.parse.urlencode({'keyword': s, 'pageIndex': 1, 'pageSize': 20, 'regionId': 6}))
        hit = [x for x in (j or {}).get('data') or [] if x.get('regionId') == 6 and s in (x.get('symbol'), x.get('disSymbol'))]
        if hit:
            W[s] = hit[0]['tickerId']; new = True
    if new:
        json.dump(W, open('wbid.json', 'w'), separators=(',', ':'), sort_keys=True)
    return W


def fetch_once():
    W = ids()
    by = {v: k for k, v in W.items()}
    L, q = list(W.values()), {}
    for i in range(0, len(L), 50):    # 한 번에 100개까지라 50개씩
        a = get('/bgw/quote/realtime?ids=' + ','.join(map(str, L[i:i + 50])) + '&includeSecu=1&delay=0&more=1')
        for x in a if isinstance(a, list) else []:
            s, r = by.get(x.get('tickerId')), quote(x)
            if s and r:
                q[s] = r
    if q:
        json.dump({'at': int(time.time()), 'q': q}, open('pre.json', 'w'), separators=(',', ':'), sort_keys=True)
    return len(q)


def git(*a):
    return subprocess.run(['git', *a], capture_output=True, text=True)


def push():
    git('add', 'pre.json', 'wbid.json')
    if git('diff', '--staged', '--quiet').returncode == 0:
        return
    git('commit', '-q', '-m', f'프리장 시세 뉴욕 {ny_now():%m-%d %H:%M} (자동)')
    for _ in range(4):
        if git('pull', '-q', '--rebase', 'origin', 'main').returncode != 0:
            git('rebase', '--abort')
        elif git('push', '-q', 'origin', 'HEAD:main').returncode == 0:
            return
        time.sleep(10)
    print('푸시 실패')


if __name__ == '__main__':
    if '--loop' not in sys.argv:
        print(fetch_once(), '기호')
        sys.exit()
    t0 = time.time()
    if not in_pre(ny_now()):
        print('프리장 시간이 아님', f'{ny_now():%a %H:%M}')
        sys.exit()
    while in_pre(ny_now()) and time.time() - t0 < LIMIT:
        n = fetch_once()
        print(f'{ny_now():%H:%M}', n, '기호')
        if n:
            push()
        time.sleep(EVERY - time.time() % EVERY + 5)   # 뉴욕 :00 · :10 · … 5초 뒤
