"""온비드 공매 아파트 × 국토부 실거래가 수집·매칭.

실행:  python collect.py
출력:  data/onbid_raw.json      온비드 입찰 회차 원본
       data/molit/<시도>.csv    국토부 실거래가 공개시스템 CSV (시도별 최근 1년)
       data/results.json        물건별 매칭 결과 (report.html이 읽는 데이터)
       data/results.csv         같은 내용의 표

API 키·로그인 없이 두 사이트의 공개 조회 기능만 사용한다.
국토부 CSV는 일일 다운로드 100회 제한이 있어 받은 파일은 재사용한다.
"""
import csv
import datetime as dt
import gzip
import http.cookiejar
import io
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
MOLIT_DIR = DATA / "molit"
SNAP = DATA / "trades.csv.gz"            # 실거래 스냅샷: 국토부에 접속할 수 없는 환경(GitHub 러너)에서 대신 쓴다
SNAP_META = DATA / "trades_meta.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

TODAY = dt.date.today()
BID_FROM = TODAY
BID_TO = TODAY + dt.timedelta(days=95)          # 앞으로 약 3개월 내 입찰 회차
TRADE_FROM = TODAY - dt.timedelta(days=364)     # 실거래 비교 구간: 최근 1년
TRADE_TO = TODAY
APT_CTGR_ID = "10201"                           # 온비드 용도 분류: 주거용건물 > 아파트

ONBID = "https://www.onbid.co.kr"
ONBID_PAGE = ONBID + "/op/cltrpbancinf/cltr/cltrcdtnsrch/CltrCdtnSrchController/mvmnCltrCdtnSrchClg.do"
ONBID_LIST = ONBID + "/op/cltrpbancinf/cltr/cltrcdtnsrch/CltrCdtnSrchController/srchCltrCdtn.do"
ONBID_DTL = ONBID + "/op/cltrpbancinf/cltrdtl/CltrDtlController/mvmnCltrDtl.do"
ONBID_LEASE = ONBID + "/op/cltrpbancinf/cltrdtl/CltrDtlController/inqLeasInfClg.do"
MOLIT = "https://rt.molit.go.kr"


def opener(referer):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA), ("Referer", referer)]
    return op


def post(op, url, params, timeout=180):
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(params).encode("utf-8"),
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
    )
    return op.open(req, timeout=timeout).read()


# ---------------------------------------------------------------- 온비드
def fetch_onbid():
    op = opener(ONBID_PAGE)
    for attempt in range(3):               # 잠깐의 끊김에 대비해 첫 접속만 다시 시도한다
        try:
            op.open(ONBID_PAGE, timeout=30).read()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(10)
    rows, page = [], 1
    while True:
        d = json.loads(post(op, ONBID_LIST, {
            "pageIndex": page, "pageUnit": 100,
            "srchCltrType": "0001",              # 부동산
            "srchArrayCtgrId": APT_CTGR_ID,
            "srchBidPerdType": "0004",           # 입찰기간 직접입력
            "srchBidPerdBgngDt": BID_FROM.isoformat(),
            "srchBidPerdEndDt": BID_TO.isoformat(),
        }).decode("utf-8"))
        batch = d.get("cltrInfVOList") or []
        rows += batch
        total = int(d.get("totalCount") or 0)
        print(f"  온비드 {page}쪽: {len(rows)}/{total}")
        if not batch or len(rows) >= total:
            break
        page += 1
        time.sleep(1.0)
    return rows


def parse_dt(s):
    try:
        return dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return None


def group_items(rows):
    """입찰 회차(행)를 물건 단위로 묶는다."""
    now = dt.datetime.now()
    by = {}
    for r in rows:
        by.setdefault(r["onbidCltrno"], []).append(r)
    items = []
    for no, rs in by.items():
        rs.sort(key=lambda r: r.get("pbctDdlnDt") or "")
        live = [r for r in rs
                if r.get("pbancPbctCltrStatNm") != "입찰마감"
                and (parse_dt(r.get("pbctDdlnDt") or "") or now) >= now
                and (r.get("lowstBidPrc") or 0) > 0]
        if not live:
            continue
        cur = live[0]
        last = min(live, key=lambda r: r["lowstBidPrc"])
        items.append({
            "id": no,
            "name": cur["onbidCltrNm"],
            "region": cur.get("sidoSgkEmd") or "",
            "sido": (cur.get("sidoSgkEmd") or "").split(" ")[0],
            "asset_type": cur.get("scrnPrptDvsnNm") or "",
            "seller": cur.get("regOrgNm") or "",
            "dispose": cur.get("dspsMthodNm") or "",
            "area": cur.get("bldSqms") or 0,
            "land_area": cur.get("landSqms") or 0,
            "appraisal": cur.get("cltrApslEvlAvgAmt") or 0,
            "fail_count": int(cur.get("uscbdCnt") or 0),
            "views": cur.get("inqCnt") or 0,
            "status": cur.get("pbancPbctCltrStatNm") or "",
            "cur_price": cur["lowstBidPrc"],
            "cur_round": cur.get("pbctNsq") or "",
            "cur_begin": cur.get("pbctBegnDtm") or "",
            "cur_end": cur.get("pbctDdlnDt") or "",
            "last_price": last["lowstBidPrc"],
            "last_end": last.get("pbctDdlnDt") or "",
            "rounds": [{"end": r.get("pbctDdlnDt") or "", "price": r["lowstBidPrc"]} for r in live],
        })
        keys = {"onbidCltrno": cur["onbidCltrno"],
                "onbidPbancNo": cur.get("onbidPbancNo") or "",
                "pbctNo": cur.get("pbctNo") or "",
                "cltrScrnGrpCd": cur.get("cltrScrnGrpCd") or "",
                "pbctCdtnNo": cur.get("pbctCdtnNo") or ""}
        items[-1]["keys"] = keys
        items[-1]["url"] = ONBID_DTL + "?" + urllib.parse.urlencode(keys)
    return items


# ---------------------------------------------------------------- 국토부 실거래가
def fetch_molit(sido_names):
    MOLIT_DIR.mkdir(parents=True, exist_ok=True)
    page = MOLIT + "/pt/xls/xls.do?mobileAt="
    op = opener(page)
    op.open(page, timeout=25).read()
    codes = {s["ctprvnNm"]: s["signguCode"]
             for s in json.loads(post(op, MOLIT + "/data/sido.do", {}).decode("utf-8"))}
    out = {}
    for name in sorted(sido_names or codes):       # 시도를 지정하지 않으면 전국
        code = codes.get(name)
        if not code:
            print(f"  [건너뜀] 국토부 시도 목록에 없는 이름: {name}")
            continue
        f = MOLIT_DIR / f"{name}_{TRADE_FROM:%Y%m%d}_{TRADE_TO:%Y%m%d}.csv"
        if not f.exists():
            raw = post(op, MOLIT + "/pt/xls/ptXlsCSVDown.do", {
                "srhThingNo": "A", "srhDelngSecd": "1", "srhAddrGbn": "1", "srhLfstsSecd": "1",
                "sidoNm": name, "sggNm": "전체", "emdNm": "전체", "loadNm": "전체",
                "areaNm": "전체", "hsmpNm": "전체", "mobileAt": "",
                "srhFromDt": TRADE_FROM.isoformat(), "srhToDt": TRADE_TO.isoformat(),
                "srhNewRonSecd": "", "srhSidoCd": code, "srhSggCd": "", "srhEmdCd": "",
                "srhRoadNm": "", "srhLoadCd": "", "srhHsmpCd": "", "srhArea": "",
                "srhFromAmount": "", "srhToAmount": "", "srhLrArea": "",
            })
            if b'"NO"' not in raw[:4000]:
                # 일부 시도만 빠진 채 매칭하면 결과가 조용히 줄어든다: 실패로 처리해 스냅샷으로 넘어가게 한다
                raise ConnectionError(f"국토부 {name}: CSV가 아닌 응답 ({len(raw)}바이트)")
            f.write_bytes(raw)
            print(f"  국토부 {name}: {len(raw) / 1e6:.1f}MB 저장")
            time.sleep(2.0)
        else:
            print(f"  국토부 {name}: 기존 파일 사용")
        out[name] = f
    return out


def save_snapshot(idx):
    """매칭에 쓰는 실거래를 압축 CSV 한 파일로 저장한다(저장소에 올려 GitHub 러너가 쓴다)."""
    n = 0
    with gzip.open(SNAP, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sgg", "bunji", "date", "price_man", "area", "floor", "complex", "built", "deal"])
        for sgg in sorted(idx):
            for bj in sorted(idx[sgg]):
                for t in idx[sgg][bj]:
                    w.writerow([sgg, bj, t["date"], t["price"] // 10000, t["area"], t["floor"],
                                t["complex"], t["built"], t["deal"]])
                    n += 1
    meta = {"saved_at": TODAY.isoformat(), "trade_from": TRADE_FROM.isoformat(),
            "trade_to": TRADE_TO.isoformat(), "rows": n}
    SNAP_META.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"  실거래 스냅샷 저장: {n:,}건 ({SNAP.stat().st_size / 1e6:.1f}MB)")


def load_snapshot():
    if not SNAP.exists() or not SNAP_META.exists():
        return None
    idx = {}
    with gzip.open(SNAP, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            idx.setdefault(r["sgg"], {}).setdefault(r["bunji"], []).append({
                "date": r["date"], "price": int(r["price_man"]) * 10000, "area": float(r["area"]),
                "floor": r["floor"], "complex": r["complex"], "built": r["built"], "deal": r["deal"]})
    return idx, json.loads(SNAP_META.read_text(encoding="utf-8"))


def norm_bunji(s):
    m = re.match(r"\s*(산)?\s*(\d+)(?:-(\d+))?", s or "")
    if not m:
        return None
    main, sub = int(m.group(2)), int(m.group(3) or 0)
    return ("산" if m.group(1) else "") + (f"{main}-{sub}" if sub else f"{main}")


def load_trades(files):
    """{시군구 문자열: {번지: [거래...]}}"""
    idx = {}
    n = 0
    for f in files.values():
        text = f.read_bytes().decode("cp949", errors="replace")
        start = text.find('"NO"')
        for row in csv.DictReader(io.StringIO(text[start:])):
            if (row.get("해제사유발생일") or "-").strip() not in ("-", ""):
                continue                                  # 계약 해제 건 제외
            try:
                price = int(row["거래금액(만원)"].replace(",", "")) * 10000
                area = float(row["전용면적(㎡)"])
                ym, day = row["계약년월"], int(row["계약일"])
                date = f"{ym[:4]}-{ym[4:6]}-{day:02d}"
            except Exception:
                continue
            bj = norm_bunji(row.get("번지"))
            if not bj:
                continue
            floor = row.get("층") or ""
            idx.setdefault(row["시군구"].strip(), {}).setdefault(bj, []).append({
                "date": date, "price": price, "area": area, "floor": floor,
                "complex": row.get("단지명") or "", "built": row.get("건축년도") or "",
                "deal": row.get("거래유형") or "",
            })
            n += 1
    print(f"  실거래 {n:,}건 적재 (계약 해제 건 제외)")
    return idx


def norm_name(item):
    """'(16) 경기 군포시 당동 885 …' 같은 표기를 '경기도 군포시 당동 885 …'로 맞춘다."""
    name = re.sub(r"^\(\d+\)\s*", "", item["name"]).strip()
    sido = item["sido"]
    first = name.split(" ")[0] if name else ""
    if sido and first != sido and first and sido.startswith(first[:2]) and len(first) <= 4:
        name = sido + name[len(first):]
    return re.sub(r"\s+", " ", name)


def match(item, idx):
    name = norm_name(item)
    best = None
    for sgg in idx:                     # 물건명 앞부분과 일치하는 가장 긴 시군구 문자열
        if name.startswith(sgg + " ") and (best is None or len(sgg) > len(best)):
            best = sgg
    rest = name[len(best):] if best else ""
    if not best:
        # 물건명이 옛 시도명·구 생략 표기일 때: 온비드가 준 지역(시도 시군구 읍면동)을 기준으로 찾는다
        region = item["region"]
        emd = region.split(" ")[-1] if region else ""
        pos = name.find(emd + " ") if emd else -1
        if pos >= 0:
            rest = name[pos + len(emd):].strip()
            ri = re.match(r"([가-힣0-9]+리)\s+(.*)", rest)
            if ri and f"{region} {ri.group(1)}" in idx:
                best, rest = f"{region} {ri.group(1)}", ri.group(2)
            elif region in idx:
                best = region
    if not best:
        return {"match": "none", "why": "주소(시군구·동) 불일치"}
    bj = norm_bunji(rest)
    trades = idx[best].get(bj) if bj else None
    if not trades:
        return {"match": "none", "why": "같은 지번의 최근 1년 실거래 없음"}
    area = item["area"]
    same = [t for t in trades if abs(t["area"] - area) <= 1.0]
    if same:
        same.sort(key=lambda t: t["date"], reverse=True)
        cut = (TODAY - dt.timedelta(days=183)).isoformat()
        recent = [t for t in same if t["date"] >= cut]
        base = recent if len(recent) >= 3 else same
        return {
            "match": "exact",
            "complex": same[0]["complex"], "built": same[0]["built"],
            "market": int(statistics.median(t["price"] for t in base)),
            "market_basis": f"동일 면적 실거래 {len(base)}건 중앙값"
                            + (" (최근 6개월)" if base is recent else " (최근 1년)"),
            "n": len(base), "lo": min(t["price"] for t in base), "hi": max(t["price"] for t in base),
            "last_date": same[0]["date"],
            "trades": [{k: t[k] for k in ("date", "price", "area", "floor")} for t in same[:30]],
        }
    if not area:
        return {"match": "none", "why": "물건 면적 정보 없음"}
    near = sorted(trades, key=lambda t: abs(t["area"] - area))
    near = [t for t in near if abs(t["area"] - area) / area <= 0.2]
    if not near:
        return {"match": "none", "why": "같은 단지에 비슷한 면적 거래 없음"}
    per = statistics.median(t["price"] / t["area"] for t in near)
    near.sort(key=lambda t: t["date"], reverse=True)
    return {
        "match": "approx",
        "complex": near[0]["complex"], "built": near[0]["built"],
        "market": int(per * area),
        "market_basis": f"같은 단지 유사 면적 {len(near)}건의 ㎡당 중앙값 × 물건 면적",
        "n": len(near), "lo": int(min(t["price"] / t["area"] for t in near) * area),
        "hi": int(max(t["price"] / t["area"] for t in near) * area),
        "last_date": near[0]["date"],
        "trades": [{k: t[k] for k in ("date", "price", "area", "floor")} for t in near[:30]],
    }


def fetch_leases(items):
    """압류재산의 임대차 정보(감정평가서·배분요구서 기준)를 읽는다."""
    op = opener(ONBID_PAGE)
    op.open(ONBID_PAGE, timeout=60).read()
    for n, it in enumerate(items, 1):
        try:
            html = post(op, ONBID_LEASE, dict(it["keys"], pageUnit=50, pageIndex=1, viewMode="PC"),
                        timeout=60).decode("utf-8", errors="replace")
        except Exception as e:
            it["lease"] = {"ok": False, "why": str(e)[:80]}
            continue
        tenants = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
            tds = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", td)).strip()
                   for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]
            if len(tds) >= 7:
                dep = re.sub(r"[^0-9]", "", tds[2])
                tenants.append({"kind": tds[0], "deposit": int(dep) if dep else None,
                                "rent": tds[3], "fixed": tds[5], "movein": tds[6]})
        known = [t["deposit"] for t in tenants if t["deposit"]]
        it["lease"] = {"ok": True, "count": len(tenants), "deposit_sum": sum(known),
                       "deposit_unknown": sum(1 for t in tenants if not t["deposit"]),
                       "tenants": tenants[:10]}
        if n % 20 == 0:
            print(f"  임대차 {n}/{len(items)}")
        time.sleep(0.4)


def flags(item):
    n = item["name"]
    out = []
    if "지분" in n:
        out.append("지분 물건 가능성")
    if item["dispose"] and item["dispose"] != "매각":
        out.append(f"처분방식: {item['dispose']}")
    if re.search(r"제?\s*(지하|B)\s*\d*\s*층|제?1층", n):
        out.append("저층(1층·지하)")
    if "외" in n and "필지" in n:
        out.append("여러 필지")
    if item["fail_count"] >= 5:
        out.append(f"유찰 {item['fail_count']}회")
    return out


def main():
    DATA.mkdir(exist_ok=True)
    print("1) 온비드 아파트 공매 회차 수집")
    rows = fetch_onbid()
    (DATA / "onbid_raw.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    items = [i for i in group_items(rows) if i["dispose"] in ("매각", "")]
    print(f"  회차 {len(rows)}건 → 매각 물건 {len(items)}건")

    print("2) 국토부 실거래가 CSV 수집")
    snap = None
    try:
        idx = load_trades(fetch_molit(None))
        save_snapshot(idx)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        got = load_snapshot()
        if got is None:
            raise
        idx, snap = got
        print(f"  국토부 사이트에 접속하지 못했습니다({e}).")
        print(f"  저장된 실거래 스냅샷을 씁니다: {snap['saved_at']} 저장, {snap['rows']:,}건")

    print("3) 매칭")
    for it in items:
        it.update(match(it, idx))
        it["flags"] = flags(it)
        if it.get("market"):
            it["disc_cur"] = round(1 - it["cur_price"] / it["market"], 4)
            it["disc_last"] = round(1 - it["last_price"] / it["market"], 4)
            it["gap_cur"] = it["market"] - it["cur_price"]
    cnt = {k: sum(1 for i in items if i["match"] == k) for k in ("exact", "approx", "none")}
    print(f"  동일 면적 매칭 {cnt['exact']} · 유사 면적 추정 {cnt['approx']} · 매칭 실패 {cnt['none']}")

    print("4) 압류재산 임대차 정보")
    target = [i for i in items if i["asset_type"] == "압류재산" and i.get("market")]
    fetch_leases(target)
    for it in target:
        ls = it.get("lease") or {}
        if ls.get("ok") and ls["count"]:
            it["flags"].append(f"임차인 {ls['count']}명")
            if ls["deposit_sum"]:
                # 최악의 경우: 신고된 보증금을 매수인이 전액 인수
                it["disc_worst"] = round(1 - (it["cur_price"] + ls["deposit_sum"]) / it["market"], 4)

    items.sort(key=lambda i: i.get("disc_cur", -9), reverse=True)
    meta = {
        "collected_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "bid_from": BID_FROM.isoformat(), "bid_to": BID_TO.isoformat(),
        "trade_from": snap["trade_from"] if snap else TRADE_FROM.isoformat(),
        "trade_to": snap["trade_to"] if snap else TRADE_TO.isoformat(),
        "trade_snapshot": snap["saved_at"] if snap else None,   # 값이 있으면 저장된 스냅샷으로 매칭한 것
        "rounds": len(rows), "items": len(items), **cnt,
    }
    (DATA / "results.json").write_text(
        json.dumps({"meta": meta, "items": items}, ensure_ascii=False), encoding="utf-8")
    with open(DATA / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["물건명", "재산유형", "매각기관", "전용면적", "감정가", "현재 최저입찰가", "입찰마감",
                    "마지막 예정 회차 최저가", "시세(실거래 중앙값)", "시세 근거", "비교 거래수",
                    "현재 할인율", "마지막 회차 할인율", "임차인 수", "신고 보증금 합계",
                    "보증금 전액 인수 시 할인율", "매칭", "주의", "링크"])
        for i in items:
            w.writerow([i["name"], i["asset_type"], i["seller"], i["area"], i["appraisal"],
                        i["cur_price"], i["cur_end"], i["last_price"], i.get("market", ""),
                        i.get("market_basis", i.get("why", "")), i.get("n", ""),
                        i.get("disc_cur", ""), i.get("disc_last", ""),
                        (i.get("lease") or {}).get("count", ""), (i.get("lease") or {}).get("deposit_sum", ""),
                        i.get("disc_worst", ""), i["match"],
                        " / ".join(i["flags"]), i["url"]])
    print("완료: data/results.json, data/results.csv")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # 종료 코드 75: 대상 사이트에 접속하지 못함(코드 오류가 아님). GitHub Actions가 다른 러너에서 다시 시도한다.
        print(f"수집 대상 사이트에 접속하지 못했습니다: {e}", flush=True)
        sys.exit(75)
