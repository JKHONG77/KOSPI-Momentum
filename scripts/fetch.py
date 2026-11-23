#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KRX Open API 일별 데이터 수집 (GitHub Actions에서 매일 실행)

- 유가증권 일별매매정보 (stk_bydd_trd)  -> data/daily/stk/YYYY/YYYYMMDD.csv.gz
- KOSPI 시리즈 일별시세 (kospi_dd_trd)  -> data/daily/idx/YYYY/YYYYMMDD.csv.gz
- 이미 받은 날짜는 건너뜀. 한 번 실행에 최대 MAX_CALLS 회 호출 후 멈춤(다음 실행에서 이어받음).
- 인증키: 환경변수 KRX_AUTH_KEY (GitHub Secrets)
"""
import csv
import datetime as dt
import gzip
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(line_buffering=True)   # GitHub 로그에 바로바로 표시
except Exception:
    pass

START = os.environ.get("START_DATE", "20100101")
MAX_CALLS = int(os.environ.get("MAX_CALLS", "4500"))   # KRX 일일 호출한도(약 1만) 고려, 하루 2회 실행
SLEEP = 0.1

ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "data" / "daily"
EMPTY = DAILY / "empty_days.txt"          # 휴장일(데이터 없음) 기록
HOSTS = ["https://data-dbg.krx.co.kr", "http://data-dbg.krx.co.kr"]
EP = {"stk": "/svc/apis/sto/stk_bydd_trd", "idx": "/svc/apis/idx/kospi_dd_trd"}
COLS = {
    "stk": [("ISU_CD", "code"), ("ISU_NM", "name"), ("SECT_TP_NM", "sect"), ("FLUC_RT", "fluc"),
            ("TDD_CLSPRC", "close"), ("MKTCAP", "mktcap"), ("ACC_TRDVAL", "trdval")],
    "idx": [("IDX_NM", "name"), ("FLUC_RT", "fluc"), ("CLSPRC_IDX", "close")],
}


class AuthError(Exception):
    pass


class StopRun(Exception):
    pass


calls = 0


def call(kind, bas_dd, key):
    global calls
    if kind == "idx" and os.environ.get("KRX_AUTH_KEY_INDEX", "").strip():
        key = os.environ["KRX_AUTH_KEY_INDEX"].strip()   # 지수용 키를 따로 받은 경우
    if calls >= MAX_CALLS:
        raise StopRun(f"이번 실행 호출한도({MAX_CALLS}) 도달")
    last = None
    for host in HOSTS:
        req = urllib.request.Request(f"{host}{EP[kind]}?basDd={bas_dd}",
                                     headers={"AUTH_KEY": key, "User-Agent": "Mozilla/5.0"})
        for attempt in range(3):
            try:
                calls += 1
                with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
                    raw = r.read().decode("utf-8", "ignore")
                time.sleep(SLEEP)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    # KRX가 가끔 빈/비정상 응답을 줌 -> 잠시 쉬고 재시도
                    last = f"비정상 응답: {raw[:120]!r}"
                    time.sleep(5 * (attempt + 1))
                    continue
                if "OutBlock_1" not in data:
                    msg = json.dumps(data, ensure_ascii=False)[:300]
                    if any(w in msg.lower() for w in ("auth", "key", "인증", "권한")):
                        raise AuthError(msg)
                    raise StopRun(f"예상치 못한 응답({kind} {bas_dd}): {msg}")
                return data["OutBlock_1"] or []
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:300]
                if e.code in (401, 403):
                    raise AuthError(f"HTTP {e.code} {body}")
                last = f"HTTP {e.code} {body}"
                if e.code == 429:
                    raise StopRun("KRX 일일 호출한도 초과로 보임 -> 다음 실행에서 이어받음")
                time.sleep(3 * (attempt + 1))
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError) as e:
                last = repr(e)
                if "SSL" in last or "CERTIFICATE" in last.upper():
                    break
                time.sleep(3 * (attempt + 1))
    raise StopRun(f"네트워크 오류({kind} {bas_dd}): {last}")


def path(kind, d):
    return DAILY / kind / d[:4] / f"{d}.csv.gz"


def save(kind, d, rows):
    p = path(kind, d)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for _, c in COLS[kind]])
        for r in rows:
            w.writerow([str(r.get(src, "")).replace(",", "").strip() for src, _ in COLS[kind]])


def main():
    key = os.environ.get("KRX_AUTH_KEY", "").strip()
    if not key:
        sys.exit("KRX_AUTH_KEY 가 없습니다. 저장소 Settings > Secrets and variables > Actions 에 등록하세요.")

    kst_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None)
    today = kst_now.date()
    empty = set(EMPTY.read_text().split()) if EMPTY.exists() else set()
    start = dt.datetime.strptime(START, "%Y%m%d").date()

    todo = []
    d = start
    while d <= today:
        s = d.strftime("%Y%m%d")
        if d.weekday() < 5 and s not in empty and not path("stk", s).exists():
            todo.append(s)
        d += dt.timedelta(days=1)
    print(f"수집 대상 평일 {len(todo)}일 (시작 {START}, 오늘 {today})")

    got, new_empty, stop_reason = 0, [], None
    try:
        for s in todo:
            rows = call("stk", s, key)
            if not rows:
                # 3일 이상 지난 날짜가 비어 있으면 휴장일로 기록 (최근 날짜는 아직 미게시일 수 있어 재시도)
                if (today - dt.datetime.strptime(s, "%Y%m%d").date()).days >= 3:
                    new_empty.append(s)
                continue
            irows = call("idx", s, key)
            save("idx", s, irows)
            save("stk", s, rows)          # stk 파일을 마지막에 저장 = 그 날짜 완료 표시
            got += 1
            if got == 1 or got % 100 == 0:
                print(f"  {s}: 종목 {len(rows)}, 지수 {len(irows)} (누적 {got}일, 호출 {calls})")
    except AuthError as e:
        print(f"\n[인증 오류] {e}\n-> KRX 인증키가 맞는지, '유가증권 일별매매정보'와 "
              f"'KOSPI 시리즈 일별시세정보' 이용신청이 승인됐는지 확인하세요.")
        sys.exit(1)
    except StopRun as e:
        stop_reason = str(e)
        print(f"\n[이번 실행 종료] {e}")
    finally:
        if new_empty:
            EMPTY.parent.mkdir(parents=True, exist_ok=True)
            with open(EMPTY, "a") as f:
                f.write("\n".join(new_empty) + "\n")
    left = len(todo) - got - len(new_empty)
    print(f"\n이번 실행: {got}일 저장, 호출 {calls}회, 남은 날짜(최근 미게시 포함) 약 {left}일")
    if got == 0 and stop_reason and "호출한도" not in stop_reason:
        print("-> 한 건도 받지 못했습니다. KRX 접속이 막혔거나 응답이 비정상입니다. 위 오류 메시지를 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
