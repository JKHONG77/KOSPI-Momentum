#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
코스피 모멘텀 분석 (Jegadeesh & Titman 1993 방식) -> docs/data.json

- 대상: 유가증권시장 보통주 (우선주·스팩·리츠 제외)
- 수익률: KRX 일별 등락률을 복리 누적 (권리락·분할 반영, 배당 제외)
- 전략: 과거 J개월 수익률로 10분위 -> 상위10%(승자) 매수, 하위10%(패자) 매도, K개월 보유 (겹치는 포트폴리오, 동일가중)
- '1주 건너뛰기': 보유 첫 달 수익에서 월초 5거래일을 제외
- 최적 전략 선택: 앞 기간(학습)에서 평균수익 1위 조합을 고르고, 뒤 기간(검증)에서 성과를 따로 표시
"""
import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "data" / "daily"
OUT = ROOT / "data" / "out" / "data.json"     # 평문 결과 (저장소에 올라가지 않음)
JS = [3, 6, 9, 12]
KS = [3, 6, 9, 12]
SPLIT_YEAR_END = 2019          # 학습: ~2019년, 검증: 2020년~
EVENT_H = 36
TOP_N = 10

NON_SECTOR = ["코스피", "KOSPI", "대형주", "중형주", "소형주", "200", "100", "50", "배당", "커버드",
              "레버리지", "인버스", "선물", "변동성", "TR", "리더스", "밸류업", "지배구조", "동일가중",
              "섹터", "ESG", "우선주"]


def load(kind):
    frames = []
    for p in sorted((DAILY / kind).glob("*/*.csv.gz")):
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        df["date"] = p.name[:8]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    for c in ("fluc", "close", "mktcap", "trdval"):
        if c in df:
            df[c] = pd.to_numeric(df[c].replace({"": None, "-": None}), errors="coerce")
    return df


def universe(stk):
    code = stk["code"].str.strip()
    name = stk["name"].fillna("")
    keep = (code.str.len() == 6) & code.str[-1].eq("0") \
        & ~name.str.contains("스팩|리츠|SPAC", regex=True)
    return stk[keep].copy()


def stats(r):
    r = pd.Series(r).dropna()
    n = len(r)
    if n < 2:
        return None
    m, sd = r.mean(), r.std(ddof=1)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    ann = float(cum.iloc[-1] ** (12 / n) - 1) if cum.iloc[-1] > 0 else -1.0
    return {"mean": float(m), "t": float(m / (sd / math.sqrt(n))) if sd > 0 else None,
            "ann": ann, "sharpe": float(m / sd * math.sqrt(12)) if sd > 0 else None,
            "mdd": mdd, "hit": float((r > 0).mean()), "n": n}


def r6(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), 6)


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return r6(float(o))
    if isinstance(o, np.integer):
        return int(o)
    return o


def main():
    stk = load("stk")
    if stk.empty:
        print("데이터 없음 -> 분석 건너뜀")
        return
    idx = load("idx")
    dates = sorted(stk["date"].unique())
    latest = dates[-1]
    print(f"거래일 {len(dates)}일 ({dates[0]} ~ {latest})")

    stk = universe(stk)
    stk = stk.dropna(subset=["fluc"])
    stk["ym"] = stk["date"].str[:6]
    stk["g"] = 1 + stk["fluc"] / 100

    # 월 내 거래일 순번 -> 월초 5거래일
    dser = pd.Series(dates)
    day_no = dser.groupby(dser.str[:6]).cumcount() + 1
    day_no = dict(zip(dates, day_no))
    stk["dno"] = stk["date"].map(day_no)

    # 완결된 월만 백테스트에 사용 (최신 월은 진행 중일 수 있어 제외)
    months_all = sorted(stk["ym"].unique())
    cur_month = latest[:6]
    months = [m for m in months_all if m < cur_month]

    grp = stk[stk["ym"].isin(months)].groupby(["ym", "code"])
    ret = (grp["g"].prod() - 1).unstack()
    wk1 = (stk[stk["ym"].isin(months) & (stk["dno"] <= 5)]
           .groupby(["ym", "code"])["g"].prod() - 1).unstack()
    ret = ret.reindex(months)
    wk1 = wk1.reindex(index=months, columns=ret.columns)
    R = ret.values                                         # T x N
    Rskip1 = (1 + R) / (1 + np.nan_to_num(wk1.values, nan=0.0)) - 1
    T, N = R.shape
    print(f"완결 월 {T}개, 종목 {N}개")

    # 코스피 지수 월수익률
    kospi_m = pd.Series(dtype=float)
    if not idx.empty:
        k = idx[idx["name"].str.strip() == "코스피"].dropna(subset=["fluc"]).copy()
        k["ym"] = k["date"].str[:6]
        kospi_m = k.groupby("ym")["fluc"].apply(lambda s: (1 + s / 100).prod() - 1)

    logR = np.log1p(R)

    def deciles_for(J):
        """formation month t 의 10분위 (0~9), 유효하지 않으면 -1"""
        csum = np.full((T, N), np.nan)
        for t in range(J - 1, T):
            win = logR[t - J + 1:t + 1]
            ok = ~np.isnan(win).any(axis=0)
            v = win.sum(axis=0)
            v[~ok] = np.nan
            csum[t] = v
        dec = np.full((T, N), -1, dtype=int)
        for t in range(T):
            v = csum[t]
            ok = ~np.isnan(v)
            if ok.sum() < 50:
                continue
            rk = pd.Series(v[ok]).rank(pct=True, method="first").values
            dec[t, ok] = np.clip(np.ceil(rk * 10).astype(int) - 1, 0, 9)
        return dec

    def cohort(dec, skip, H):
        """D[t, h-1, d] = formation t 코호트의 t+h 월 d분위 평균수익"""
        D = np.full((T, H, 10), np.nan)
        for t in range(T):
            if (dec[t] >= 0).sum() == 0:
                continue
            for h in range(1, H + 1):
                m = t + h
                if m >= T:
                    break
                row = Rskip1[m] if (skip and h == 1) else R[m]
                for d in range(10):
                    sel = (dec[t] == d)
                    vals = row[sel]
                    vals = vals[~np.isnan(vals)]
                    if len(vals):
                        D[t, h - 1, d] = vals.mean()
        return D

    def strategy(D, K):
        """월 m 수익 = 최근 K개 코호트 평균 (분위별)"""
        S = np.full((T, 10), np.nan)
        for m in range(T):
            parts = []
            for h in range(1, K + 1):
                t = m - h
                if t < 0:
                    parts = None
                    break
                parts.append(D[t, h - 1])
            if parts is None:
                continue
            P = np.array(parts)
            if np.isnan(P).all(axis=1).any():
                continue
            S[m] = np.nanmean(P, axis=0)
        return S

    split_idx = next((i for i, m in enumerate(months) if int(m[:4]) > SPLIT_YEAR_END), None)
    if split_idx is None or split_idx < 48 or T - split_idx < 24:
        split_idx = int(T * 0.6)
    split_label = months[split_idx] if T else None

    grid, cache = [], {}
    for J in JS:
        dec = deciles_for(J)
        for skip in (0, 1):
            D = cohort(dec, skip, max(KS) if J != 0 else 12)
            for K in KS:
                S = strategy(D, K)
                spread = S[:, 9] - S[:, 0]
                s_all, s_tr, s_te = stats(spread), stats(spread[:split_idx]), stats(spread[split_idx:])
                grid.append({"J": J, "K": K, "skip": skip, "all": s_all, "train": s_tr, "test": s_te})
                cache[(J, K, skip)] = S
        cache[("dec", J)] = dec
        print(f"  J={J} 완료")

    valid = [g for g in grid if g["train"] and g["train"]["n"] >= 24]
    best = max(valid, key=lambda g: g["train"]["mean"])
    J, K, skip = best["J"], best["K"], best["skip"]
    print(f"최적(학습구간): J={J} K={K} skip={skip} 월 {best['train']['mean']:.4%}")
    S = cache[(J, K, skip)]
    first = next(i for i in range(T) if not np.isnan(S[i]).any())
    ms = months[first:]
    km = kospi_m.reindex(ms).values if len(kospi_m) else np.full(len(ms), np.nan)
    series = {
        "months": ms,
        "spread": (S[first:, 9] - S[first:, 0]).tolist(),
        "winners": S[first:, 9].tolist(),
        "losers": S[first:, 0].tolist(),
        "kospi": km.tolist(),
    }
    dec_means = [float(np.nanmean(S[first:, d])) for d in range(10)]

    # 이벤트 타임 (형성 후 1~36개월 승자-패자)
    Dev = cohort(cache[("dec", J)], skip, EVENT_H)
    ev = Dev[:, :, 9] - Dev[:, :, 0]
    ev_mean = np.nanmean(ev, axis=0)
    ev_t = []
    for h in range(EVENT_H):
        v = ev[:, h][~np.isnan(ev[:, h])]
        ev_t.append(float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))) if len(v) > 2 and v.std() > 0 else None)

    # ---------- 현재 상위/하위 종목 (일별 데이터로 계산) ----------
    latest_d = dt.datetime.strptime(latest, "%Y%m%d").date()
    start_d = (pd.Timestamp(latest_d) - pd.DateOffset(months=J)).strftime("%Y%m%d")
    win_dates = [d for d in dates if d > start_d]
    end_dates = win_dates[:-5] if skip and len(win_dates) > 5 else win_dates
    w = stk[stk["date"].isin(end_dates)]
    first_day, last_day = end_dates[0], end_dates[-1]
    present_first = set(stk.loc[stk["date"] == first_day, "code"])
    present_last = set(stk.loc[stk["date"] == latest, "code"])
    cum = w.groupby("code")["g"].prod() - 1
    cum = cum[cum.index.isin(present_first & present_last)]
    lastrow = stk[stk["date"] == latest].set_index("code")
    d1m = (pd.Timestamp(latest_d) - pd.DateOffset(months=1)).strftime("%Y%m%d")
    r1m = stk[stk["date"] > d1m].groupby("code")["g"].prod() - 1

    def rows(codes):
        out = []
        for c in codes:
            lr = lastrow.loc[c]
            out.append({"code": c, "name": lr["name"], "ret": float(cum[c]), "r1m": float(r1m.get(c, np.nan)),
                        "close": float(lr["close"]) if pd.notna(lr["close"]) else None,
                        "mktcap": float(lr["mktcap"]) if pd.notna(lr["mktcap"]) else None})
        return out

    ranked = cum.sort_values(ascending=False)
    current = {"window_start": first_day, "window_end": last_day, "n_universe": int(len(cum)),
               "top": rows(ranked.index[:TOP_N]), "bottom": rows(ranked.index[::-1][:TOP_N]),
               "decile_cut_top": float(ranked.quantile(0.9)), "decile_cut_bottom": float(ranked.quantile(0.1))}

    # ---------- 업종 ----------
    sectors = []
    if not idx.empty:
        ix = idx.dropna(subset=["fluc"]).copy()
        ix["name"] = ix["name"].str.strip()
        names = [n for n in ix.loc[ix["date"] == latest, "name"].unique()
                 if not any(p in n for p in NON_SECTOR) and n != "제조"]
        ix = ix[ix["name"].isin(names)]
        ix["g"] = 1 + ix["fluc"] / 100

        def per(months_back, end_list=None):
            s = (pd.Timestamp(latest_d) - pd.DateOffset(months=months_back)).strftime("%Y%m%d")
            sub = ix[(ix["date"] > s) & (ix["date"].isin(end_list) if end_list is not None else True)]
            ok = sub.groupby("name")["date"].min() <= min(d for d in dates if d > s)
            r = sub.groupby("name")["g"].prod() - 1
            return r[ok.reindex(r.index).fillna(False)]

        rJ = per(J, end_dates)
        r1, r3, r6_, r12 = per(1), per(3), per(6), per(12)
        for n in rJ.sort_values(ascending=False).index:
            sectors.append({"name": n, "retJ": float(rJ[n]), "r1": r1.get(n), "r3": r3.get(n),
                            "r6": r6_.get(n), "r12": r12.get(n)})

    out = {
        "generated_at": (dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None)).strftime("%Y-%m-%d %H:%M KST"),
        "data_start": dates[0], "data_end": latest, "n_trading_days": len(dates),
        "backtest_start": ms[0] if ms else None, "backtest_end": months[-1] if months else None,
        "split_month": split_label, "split_year_end": SPLIT_YEAR_END,
        "best": {"J": J, "K": K, "skip": skip, "all": best["all"], "train": best["train"], "test": best["test"]},
        "grid": grid, "series": series, "deciles": dec_means,
        "event": {"mean": ev_mean.tolist(), "t": ev_t},
        "current": current, "sectors": sectors,
        "paper": {"spread_6_6": 0.0095, "best": "12/3 (1주 건너뛰기) 월 1.49%"},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(clean(out), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {OUT} ({OUT.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
