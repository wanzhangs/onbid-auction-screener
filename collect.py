"""온비드 공매 아파트 × 국토부 실거래가 매칭.

실행:  python update_trades.py   (국토부 실거래 스냅샷 갱신, 오늘 받지 않은 시도만 다시 받는다)
       python collect.py
출력:  data/onbid_raw.json      온비드 입찰 회차 원본
       data/results.json        물건별 매칭 결과 (report.html이 읽는 데이터)
       data/results.csv         같은 내용의 표

API 키·로그인 없이 온비드의 공개 조회 기능만 사용한다.
실거래가는 update_trades.py가 받아 둔 data/trades/ 스냅샷을 읽는다(이 스크립트는 국토부에 접속하지 않는다).
종료 코드 75: 온비드에 접속하지 못함(GitHub Actions가 다른 러너에서 다시 시도한다).
"""
import csv
import datetime as dt
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

from update_trades import load_snapshots, norm_bunji

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

TODAY = dt.date.today()
BID_FROM = TODAY
BID_TO = TODAY + dt.timedelta(days=95)          # 앞으로 약 3개월 내 입찰 회차
APT_CTGR_ID = "10201"                           # 온비드 용도 분류: 주거용건물 > 아파트

ONBID = "https://www.onbid.co.kr"
ONBID_PAGE = ONBID + "/op/cltrpbancinf/cltr/cltrcdtnsrch/CltrCdtnSrchController/mvmnCltrCdtnSrchClg.do"
ONBID_LIST = ONBID + "/op/cltrpbancinf/cltr/cltrcdtnsrch/CltrCdtnSrchController/srchCltrCdtn.do"
ONBID_DTL = ONBID + "/op/cltrpbancinf/cltrdtl/CltrDtlController/mvmnCltrDtl.do"
ONBID_LEASE = ONBID + "/op/cltrpbancinf/cltrdtl/CltrDtlController/inqLeasInfClg.do"


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


# ---------------------------------------------------------------- 매칭
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

    print("2) 실거래 스냅샷 읽기 (data/trades)")
    idx, tmeta, n = load_snapshots()
    if not n:
        sys.exit("실거래 스냅샷이 없습니다. 먼저 python update_trades.py 를 실행하세요.")
    saved = sorted(m["saved_at"] for m in tmeta.values())
    print(f"  {len(tmeta)}개 시도 · {n:,}건 · 저장일 {saved[0]} ~ {saved[-1]}")

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
        "trade_from": min(m["trade_from"] for m in tmeta.values()),
        "trade_to": max(m["trade_to"] for m in tmeta.values()),
        "trade_saved": saved[0] if saved[0] == saved[-1] else f"{saved[0]} ~ {saved[-1]}",   # 시도별 스냅샷 저장일
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
