"""국토부 실거래가(아파트 매매, 최근 1년)를 시도별 스냅샷으로 받아 둔다.

실행:
    python update_trades.py                 저장한 지 MAX_AGE일(기본 28일)이 지난 시도만 다시 받는다
    python update_trades.py --max-age 0     전부 다시 받는다
    python update_trades.py --limit 3       이번 실행에서 최대 3개 시도만 받는다
    python update_trades.py --fail-after 45 아무것도 못 받았고 가장 오래된 스냅샷이 45일을 넘으면 오류(1)로 끝낸다

출력:
    data/trades/<시도>.csv.gz   매칭에 쓰는 실거래 (계약 해제 건 제외)
    data/trades/meta.json       시도별 저장일·기간·건수

collect.py는 이 폴더만 읽는다. 국토부 사이트는 해외 IP(GitHub 러너)에서 접속이 됐다 안 됐다 하므로,
받을 수 있을 때 받은 시도만 저장하고 다음 실행이 남은 시도를 이어서 받는다.
종료 코드: 0 = 받았거나 받을 것이 없음, 75 = 접속하지 못해 아무것도 못 받음(다른 러너에서 재시도), 1 = 오류.
"""
import csv
import datetime as dt
import gzip
import http.client
import http.cookiejar
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRADE_DIR = HERE / "data" / "trades"
META = TRADE_DIR / "meta.json"
SIDOS = TRADE_DIR / "sidos.json"          # 국토부가 알려 준 시도 이름 목록(마지막으로 접속했을 때 기준)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MOLIT = "https://rt.molit.go.kr"
PAGE = MOLIT + "/pt/xls/xls.do?mobileAt="
TODAY = dt.date.today()
TRADE_FROM = TODAY - dt.timedelta(days=364)     # 시도 단위 다운로드는 최대 1년
TRADE_TO = TODAY
MAX_AGE = 28
COLS = ["sgg", "bunji", "date", "price_man", "area", "floor", "complex", "built", "deal"]
NET_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)


def norm_bunji(s):
    m = re.match(r"\s*(산)?\s*(\d+)(?:-(\d+))?", s or "")
    if not m:
        return None
    main, sub = int(m.group(2)), int(m.group(3) or 0)
    return ("산" if m.group(1) else "") + (f"{main}-{sub}" if sub else f"{main}")


def load_meta():
    return json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}


def age(meta, name):
    m = meta.get(name)
    return None if not m else (TODAY - dt.date.fromisoformat(m["saved_at"])).days


def session():
    """국토부 다운로드 화면을 열고 시도 코드를 받는다."""
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA), ("Referer", PAGE)]
    op.open(PAGE, timeout=25).read()
    req = urllib.request.Request(MOLIT + "/data/sido.do", data=b"", headers={"X-Requested-With": "XMLHttpRequest"})
    codes = {s["ctprvnNm"]: s["signguCode"] for s in json.loads(op.open(req, timeout=25).read().decode("utf-8"))}
    return op, codes


def download(op, name, code):
    body = urllib.parse.urlencode({
        "srhThingNo": "A", "srhDelngSecd": "1", "srhAddrGbn": "1", "srhLfstsSecd": "1",
        "sidoNm": name, "sggNm": "전체", "emdNm": "전체", "loadNm": "전체",
        "areaNm": "전체", "hsmpNm": "전체", "mobileAt": "",
        "srhFromDt": TRADE_FROM.isoformat(), "srhToDt": TRADE_TO.isoformat(),
        "srhNewRonSecd": "", "srhSidoCd": code, "srhSggCd": "", "srhEmdCd": "",
        "srhRoadNm": "", "srhLoadCd": "", "srhHsmpCd": "", "srhArea": "",
        "srhFromAmount": "", "srhToAmount": "", "srhLrArea": "",
    }).encode("utf-8")
    req = urllib.request.Request(MOLIT + "/pt/xls/ptXlsCSVDown.do", data=body,
                                 headers={"X-Requested-With": "XMLHttpRequest"})
    raw = op.open(req, timeout=180).read()
    if b'"NO"' not in raw[:4000]:
        raise ConnectionError(f"CSV가 아닌 응답 ({len(raw)}바이트)")
    return raw


def parse(raw):
    text = raw.decode("cp949", errors="replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text[text.find('"NO"'):])):
        if (row.get("해제사유발생일") or "-").strip() not in ("-", ""):
            continue                                  # 계약 해제 건 제외
        try:
            price = int(row["거래금액(만원)"].replace(",", ""))
            area = float(row["전용면적(㎡)"])
            ym, day = row["계약년월"], int(row["계약일"])
        except Exception:
            continue
        bj = norm_bunji(row.get("번지"))
        if not bj:
            continue
        rows.append([row["시군구"].strip(), bj, f"{ym[:4]}-{ym[4:6]}-{day:02d}", price, area,
                     row.get("층") or "", row.get("단지명") or "", row.get("건축년도") or "",
                     row.get("거래유형") or ""])
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return rows


def write_sido(name, rows, meta):
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLS)
    w.writerows(rows)
    with open(TRADE_DIR / f"{name}.csv.gz", "wb") as f:      # mtime=0: 내용이 같으면 파일도 같게
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as g:
            g.write(buf.getvalue().encode("utf-8"))
    meta[name] = {"saved_at": TODAY.isoformat(), "trade_from": TRADE_FROM.isoformat(),
                  "trade_to": TRADE_TO.isoformat(), "rows": len(rows)}
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def load_snapshots():
    """{시군구 문자열: {번지: [거래...]}}, 메타"""
    meta, idx, n = load_meta(), {}, 0
    for name in meta:
        f = TRADE_DIR / f"{name}.csv.gz"
        if not f.exists():
            continue
        with gzip.open(f, "rt", encoding="utf-8", newline="") as g:
            for r in csv.DictReader(g):
                idx.setdefault(r["sgg"], {}).setdefault(r["bunji"], []).append({
                    "date": r["date"], "price": int(r["price_man"]) * 10000, "area": float(r["area"]),
                    "floor": r["floor"], "complex": r["complex"], "built": r["built"], "deal": r["deal"]})
                n += 1
    return idx, meta, n


def opt(args, name, default):
    return int(args[args.index(name) + 1]) if name in args else default


def main():
    args = sys.argv[1:]
    max_age, limit, fail_after = opt(args, "--max-age", MAX_AGE), opt(args, "--limit", 99), opt(args, "--fail-after", None)
    meta = load_meta()

    def give_up(why):
        ages = [age(meta, n) for n in meta]
        oldest = max(ages) if ages else None
        print(f"국토부 실거래가를 받지 못했습니다: {why}", flush=True)
        print(f"기존 스냅샷: {len(meta)}개 시도, 가장 오래된 것 {oldest if oldest is not None else '-'}일 전", flush=True)
        if fail_after is not None and (oldest is None or oldest > fail_after):
            print(f"스냅샷이 {fail_after}일보다 오래됐습니다. PC에서 python update_trades.py 를 실행해 푸시하세요.", flush=True)
            sys.exit(1)
        sys.exit(75)

    known = json.loads(SIDOS.read_text(encoding="utf-8")) if SIDOS.exists() else []
    if known and all(age(meta, n) is not None and age(meta, n) < max_age for n in known):
        print(f"모든 시도({len(known)}개)의 스냅샷이 {max_age}일 이내입니다. 받을 것이 없습니다.")
        return
    try:
        op, codes = session()
    except NET_ERRORS as e:
        give_up(e)
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    SIDOS.write_text(json.dumps(sorted(codes), ensure_ascii=False), encoding="utf-8")
    stale = [n for n in codes if age(meta, n) is None or age(meta, n) >= max_age]
    stale.sort(key=lambda n: -(age(meta, n) if age(meta, n) is not None else 10 ** 6))   # 오래된 것부터
    stale = stale[:limit]
    print(f"받을 시도 {len(stale)}개: {', '.join(stale)}", flush=True)
    done, fails = 0, 0
    for name in stale:
        raw = None
        for attempt in range(2):
            try:
                raw = download(op, name, codes[name])
                break
            except NET_ERRORS as e:
                print(f"  {name}: 실패 ({e})", flush=True)
                time.sleep(6)
                try:
                    op, codes = session()             # 끊긴 뒤에는 세션을 새로 연다
                except NET_ERRORS:
                    break
        if raw is None:
            fails += 1
            if fails >= 3:                            # 연달아 안 되면 이 실행에서는 그만둔다
                print("  연속 실패로 중단합니다. 남은 시도는 다음 실행에서 받습니다.", flush=True)
                break
            continue
        fails = 0
        rows = parse(raw)
        write_sido(name, rows, meta)
        done += 1
        print(f"  {name}: {len(rows):,}건 저장 ({len(raw) / 1e6:.1f}MB 수신)", flush=True)
        time.sleep(3)
    left = [n for n in codes if age(meta, n) is None or age(meta, n) >= max_age]
    print(f"이번 실행: {done}개 시도 갱신 · 아직 오래된 시도 {len(left)}개", flush=True)
    if not done and stale:
        give_up("받을 시도가 있었지만 하나도 받지 못함")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
