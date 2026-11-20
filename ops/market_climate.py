"""ดัชนีบรรยากาศการลงทุน (Market Climate Index) — แปลง "ความรู้สึกตลาด" แบบ Buffett/Munger เป็นตัวเลข แล้ว backtest

แนวคิด (Buffett: "be fearful when others are greedy, greedy when others are fearful"):
  วัดบรรยากาศตลาด 4 มิติ แต่ละตัวแปลงเป็นเปอร์เซ็นไทล์เทียบอดีตของตัวเอง (ใช้เฉพาะข้อมูลที่รู้ ณ วันนั้น)
  แล้วกลับทิศให้ "สูง = กลัว/ถูก = จังหวะดีที่จะลงทุน" · คะแนนรวม 0–100 = ค่าเฉลี่ยของ 4 มิติ
    1. มูลค่า (ช้า)      CAPE (Shiller) · Buffett Indicator (มูลค่าหุ้นทั้งประเทศ ÷ GDP) · ส่วนต่างผลตอบแทนหุ้น−พันธบัตร
    2. ความกลัว/ข่าว     VIX · ส่วนต่างดอกเบี้ยหุ้นกู้ BAA−10Y (High Yield ตัดออก: FRED ให้แค่ 3 ปีล่าสุดแล้ว) · ดัชนีข่าวรายวัน (SF Fed: ให้คะแนนบทความ
                         ข่าวเศรษฐกิจทุกวันจากหนังสือพิมพ์ใหญ่ 24 ฉบับ) · ดัชนีความไม่แน่นอนเชิงนโยบายจากข่าวรายวัน (EPU)
    3. ราคา/ความกว้าง    ระยะลงจากจุดสูงสุด 1 ปี · ระยะห่างเส้น 200 วัน · % หุ้นที่อยู่เหนือเส้น 200 วัน (breadth)
    4. ภาวะการเงิน       ความชันเส้นอัตราผลตอบแทน (10ปี−2ปี) · ดัชนีภาวะการเงิน NFCI (Chicago Fed)
  ทิศของทุกตัวกำหนดไว้ก่อนดูผล (ตามหลัก contrarian) — ไม่เลือกทิศตามผล backtest กัน overfit

Backtest:
  • IC (สหสัมพันธ์อันดับ) ระหว่างคะแนน กับ ผลตอบแทน S&P 500 รวมปันผล 1 ปี/3 ปีข้างหน้า แยกช่วงทดสอบ (≤2009) / ช่วงยืนยัน (≥2010)
  • ผลตอบแทนเฉลี่ยตามโซนคะแนน (โลภมาก → กลัวมาก)
  • กลยุทธ์จังหวะลงทุน (เงินเข้าเดือนละ 1 หน่วย · เงินสดได้ดอกเบี้ยพันธบัตร 3 เดือนจริง):
      S0 DCA ทุกเดือน 100%
      S1 DCA 70% + สำรอง 30% → ลงหมดเมื่อคะแนน ≥ 80 (ตลาดกลัวมาก)
      S2 จังหวะแบบ Buffett: คะแนน < 20 (โลภมาก) ลงแค่ครึ่งเก็บที่เหลือ · 20–80 ลงปกติ · ≥ 80 ลงปกติ + ทุ่มเงินที่เก็บไว้ทั้งหมด
      S3 เหมือน S2 แต่ใช้คะแนนจาก VIX อย่างเดียว (เทียบว่าการรวมหลายศาสตร์ช่วยจริงไหม)
      S4 จัดสัดส่วนหุ้น/เงินสด (เงินก้อน): หุ้น = 50% + 50% × คะแนน · ปรับทุกเดือน เทียบถือหุ้น 100%
แหล่งข้อมูล: Yahoo (^SP500TR ^GSPC ^VIX) · FRED · SF Fed Daily News Sentiment · Shiller ie_data · ราคาหุ้นใน data/backtest/daily
รัน: python ops/market_climate.py            (ดึงข้อมูลครั้งแรก ~1–3 นาที แคชที่ data/climate/)
     python ops/market_climate.py --no-fetch
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "climate"
OUT = DATA / "climate_results.json"
DAILY = ROOT / "data" / "backtest" / "daily"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-script"}
MIN_HIST = 1260          # ต้องมีประวัติ ≥ ~5 ปีก่อนให้คะแนน
SPLIT = "2010-01-01"     # ก่อนนี้ = ช่วงทดสอบ · หลังนี้ = ช่วงยืนยัน (out-of-sample)
SIM_START = "1996-01-01"

FRED = {"BAMLH0A0HYM2": "hy", "BAA10Y": "baa", "T10Y2Y": "curve", "NFCI": "nfci", "USEPUINDXD": "epu",
        "NCBEILQ027S": "mktval", "GDP": "gdp", "DGS10": "dgs10", "DTB3": "tbill"}


# ── ดึงข้อมูล ───────────────────────────────────────────────────────────────────
def _get(url: str, timeout: int = 60) -> bytes:
    # FRED ตอบช้า/ค้างกับ User-Agent แบบเบราว์เซอร์จากสคริปต์ แต่ตอบทันทีกับ UA แบบเครื่องมือบรรทัดคำสั่ง
    headers = {"User-Agent": "curl/8.0"} if "stlouisfed" in url else UA
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout).read()


def fetch_all() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for sid in FRED:
        # FRED มัก timeout ถ้าขอช่วงยาวครั้งเดียว → ขอทีละช่วงปีแล้วต่อกัน (มี retry)
        parts, header = [], None
        for a, b in (("1970-01-01", "1989-12-31"), ("1990-01-01", "2004-12-31"),
                     ("2005-01-01", "2014-12-31"), ("2015-01-01", "2030-12-31")):
            for attempt in range(3):
                try:
                    txt = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={a}&coed={b}",
                               timeout=90).decode()
                    break
                except Exception:  # noqa: BLE001
                    if attempt == 2:
                        raise
            lines = txt.strip().splitlines()
            header = lines[0]
            parts += lines[1:]
        (DATA / f"fred_{sid}.csv").write_text(header + "\n" + "\n".join(parts) + "\n", encoding="utf-8")
        print(f"  FRED {sid}: {len(parts)} แถว", flush=True)
    for sym in ("^SP500TR", "^GSPC", "^VIX"):
        p1, p2 = int(dt.datetime(1985, 1, 1).timestamp()), int(dt.datetime.now().timestamp())
        raw = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                   f"?interval=1d&period1={p1}&period2={p2}&includeAdjustedClose=true")
        (DATA / f"yh_{sym.strip('^')}.json").write_bytes(raw)
    (DATA / "sf_news.xlsx").write_bytes(_get("https://www.frbsf.org/wp-content/uploads/news_sentiment_data.xlsx"))
    (DATA / "shiller.xls").write_bytes(_get("http://www.econ.yale.edu/~shiller/data/ie_data.xls"))
    print("ดึงข้อมูลครบ")


def _yahoo(name: str) -> pd.Series:
    res = json.loads((DATA / f"yh_{name}.json").read_text())["chart"]["result"][0]
    idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or res["indicators"]["quote"][0]["close"]
    return pd.Series(adj, index=idx, dtype=float).dropna().groupby(level=0).last()


def _fred(sid: str) -> pd.Series:
    df = pd.read_csv(DATA / f"fred_{sid}.csv")
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(df.iloc[:, 0])
    s = s[~s.index.duplicated(keep="last")]
    return s.dropna()


def _news() -> pd.Series:
    df = pd.read_excel(DATA / "sf_news.xlsx", sheet_name="Data")
    return pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0])).dropna()


def _cape() -> pd.Series:
    raw = pd.read_excel(DATA / "shiller.xls", sheet_name="Data", header=None)
    hdr = next(i for i in range(20) if str(raw.iloc[i, 0]).strip() == "Date")
    cols = [str(c).strip() for c in raw.iloc[hdr]]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = cols
    ci = cols.index("CAPE")
    df = df[pd.to_numeric(df["Date"], errors="coerce").notna()]
    d = pd.to_numeric(df["Date"])
    yr = d.astype(int)
    mo = ((d - yr) * 100).round().astype(int).clip(1, 12)
    s = pd.Series(pd.to_numeric(df.iloc[:, ci], errors="coerce").values,
                  index=pd.to_datetime(dict(year=yr, month=mo, day=1)))
    return s.dropna()


def _breadth() -> pd.Series:
    """% ของหุ้นที่ราคาอยู่เหนือเส้นเฉลี่ย 200 วัน (จากแคชราคาหุ้น S&P 500 วันนี้ — มี survivorship)."""
    cols = {}
    for f in DAILY.glob("*.json"):
        if f.stem in ("SPY", "QQQ"):
            continue
        bars = json.loads(f.read_text(encoding="utf-8"))
        if len(bars) < 400:
            continue
        s = pd.Series([b[1] for b in bars], index=pd.to_datetime([b[0] for b in bars]))
        cols[f.stem] = (s > s.rolling(200).mean()).astype(float).where(s.rolling(200).count() == 200)
    df = pd.DataFrame(cols)
    n = df.notna().sum(axis=1)
    return (df.sum(axis=1) / n).where(n >= 150).dropna() * 100


# ── สร้างตัวแปรรายวัน (มีระยะประกาศข้อมูล กันมองอนาคต) ───────────────────────────
def lagged(s: pd.Series, days: int, index: pd.DatetimeIndex) -> pd.Series:
    s = s.copy()
    s.index = s.index + pd.Timedelta(days=days)
    return s.groupby(level=0).last().reindex(index.union(s.index)).ffill().reindex(index)


def build() -> pd.DataFrame:
    tr = _yahoo("SP500TR")
    px = _yahoo("GSPC").reindex(tr.index).ffill()
    vix = _yahoo("VIX")
    idx = tr.index
    f = {k: _fred(k) for k in FRED}
    cape = _cape()
    df = pd.DataFrame(index=idx)
    df["tr"] = tr
    df["px"] = px
    df["tbill"] = lagged(f["DTB3"], 1, idx)
    # ── คะแนนแต่ละตัว = เปอร์เซ็นไทล์เทียบ "ประวัติทั้งหมดของตัวมันเองที่มีถึงวันนั้น" (ไม่ใช่แค่ตั้งแต่ปี 1988)
    #    คำนวณบนความถี่ดั้งเดิมของข้อมูล แล้วค่อยเลื่อนตามระยะประกาศ → ไม่มองอนาคต · ทิศ: สูง = กลัว/ถูก
    def pct(x: pd.Series, min_n: int) -> pd.Series:
        return x.dropna().expanding(min_periods=min_n).rank(pct=True) * 100

    gspc_full = _yahoo("GSPC")
    dgs10_m = f["DGS10"].resample("MS").mean()
    df["cape"] = lagged(pct(-cape, 120), 35, idx)                                   # ประวัติตั้งแต่ปี 1881
    mv = f["NCBEILQ027S"] / f["GDP"].reindex(f["NCBEILQ027S"].index).ffill()
    df["buffett"] = lagged(pct(-mv, 40), 150, idx)                                  # รายไตรมาสตั้งแต่ปี 1945
    df["erp"] = lagged(pct((1 / cape * 100 - dgs10_m.reindex(cape.index)).dropna(), 120), 35, idx)  # ตั้งแต่ปี 1962
    df["vix"] = lagged(pct(vix.rolling(5).mean(), 1260), 1, idx)                    # ตั้งแต่ปี 1990
    df["baa"] = lagged(pct(f["BAA10Y"], 1260), 1, idx)                              # ตั้งแต่ปี 1986
    df["news"] = lagged(pct(-_news().rolling(21, min_periods=10).mean(), 1260), 7, idx)   # ตั้งแต่ปี 1980
    df["epu"] = lagged(pct(f["USEPUINDXD"].rolling(21, min_periods=10).mean(), 1260), 1, idx)  # ตั้งแต่ปี 1985
    df["drawdown"] = pct(-(gspc_full / gspc_full.rolling(252, min_periods=60).max() - 1), 1260).reindex(idx).ffill()
    df["dist200"] = pct(-(gspc_full / gspc_full.rolling(200).mean() - 1), 1260).reindex(idx).ffill()
    df["breadth"] = lagged(pct(-_breadth(), 1260), 1, idx)
    df["curve"] = lagged(pct(f["T10Y2Y"], 1260), 1, idx)                            # ตั้งแต่ปี 1976
    df["nfci"] = lagged(pct(f["NFCI"], 260), 6, idx)                                # รายสัปดาห์ตั้งแต่ปี 1971
    return df


GROUPS = {"มูลค่า": ["cape", "buffett", "erp"],
          "ความกลัว/ข่าว": ["vix", "baa", "news", "epu"],
          "ราคา/ความกว้าง": ["drawdown", "dist200", "breadth"],
          "ภาวะการเงิน": ["curve", "nfci"]}
LABELS = {"cape": "CAPE (Shiller P/E)", "buffett": "Buffett Indicator", "erp": "ส่วนต่างผลตอบแทนหุ้น−พันธบัตร",
          "vix": "VIX (ความกลัว)", "baa": "ส่วนต่างหุ้นกู้ BAA",
          "news": "ข่าวรายวัน (SF Fed)", "epu": "ความไม่แน่นอนจากข่าว (EPU)", "drawdown": "ลงจากยอด 1 ปี",
          "dist200": "ห่างเส้น 200 วัน", "breadth": "% หุ้นเหนือเส้น 200 วัน", "curve": "ความชัน Yield Curve",
          "nfci": "ภาวะการเงิน NFCI"}


def score(df: pd.DataFrame) -> pd.DataFrame:
    """เปอร์เซ็นไทล์แบบ expanding (เทียบเฉพาะอดีต ณ วันนั้น) → 0–100."""
    sc = pd.DataFrame(index=df.index)
    for k in LABELS:
        sc[k] = df[k]  # เป็นเปอร์เซ็นไทล์แล้วตั้งแต่ build()
    for g, ks in GROUPS.items():
        sc[g] = sc[ks].mean(axis=1, skipna=True)
    sc["รวม"] = sc[list(GROUPS)].mean(axis=1, skipna=True)
    sc["VIX อย่างเดียว"] = sc["vix"]
    return sc


# ── Backtest ───────────────────────────────────────────────────────────────────
def forward(tr: pd.Series, days: int) -> pd.Series:
    return tr.shift(-days) / tr - 1


def ic_table(sc: pd.DataFrame, tr: pd.Series) -> dict:
    out = {}
    f1, f3 = forward(tr, 252), forward(tr, 756)
    for k in list(LABELS) + list(GROUPS) + ["รวม"]:
        row = {}
        for name, lo, hi in (("is", None, SPLIT), ("oos", SPLIT, None)):
            m = sc[k].notna()
            if lo:
                m &= sc.index >= lo
            if hi:
                m &= sc.index < hi
            for hn, fr in (("1y", f1), ("3y", f3)):
                mm = m & fr.notna()
                row[f"{name}_{hn}"] = float(sc[k][mm].rank().corr(fr[mm].rank())) if mm.sum() > 500 else None  # Spearman = Pearson ของอันดับ (ไม่ต้องใช้ scipy)
        out[k] = row
    return out


def zones(s: pd.Series, tr: pd.Series) -> list[dict]:
    f1, f3 = forward(tr, 252), forward(tr, 756)
    rows = []
    for lo, hi, lab in ((0, 20, "โลภมาก"), (20, 40, "โลภ"), (40, 60, "ปกติ"), (60, 80, "กลัว"), (80, 101, "กลัวมาก")):
        m = (s >= lo) & (s < hi)
        rows.append({"zone": lab, "days_pct": float(m.mean() * 100),
                     "fwd1y": float(f1[m].mean()) if f1[m].notna().any() else None,
                     "fwd3y_ann": float(((1 + f3[m]) ** (1 / 3) - 1).mean()) if f3[m].notna().any() else None,
                     "win1y": float((f1[m] > 0).mean()) if f1[m].notna().any() else None})
    return rows


def simulate(df: pd.DataFrame, s: pd.Series | None, mode: str, start: str) -> dict:
    d = df[df.index >= start]
    tr, tb = d["tr"].values, d["tbill"].ffill().fillna(0).values / 100
    sv = s.reindex(d.index).values if s is not None else None
    sh = cash = 0.0
    month = None
    vals = []
    for i in range(len(d)):
        cash *= 1 + tb[i] / 252
        sc = sv[i] if sv is not None and not np.isnan(sv[i]) else 50.0
        if d.index[i].month != month:
            month = d.index[i].month
            if mode == "S0":
                sh += 1 / tr[i]
            elif mode == "S1":
                sh += 0.7 / tr[i]
                cash += 0.3
            else:  # S2/S3
                if sc < 20:
                    sh += 0.5 / tr[i]
                    cash += 0.5
                else:
                    sh += 1 / tr[i]
        if mode in ("S1", "S2", "S3") and sc >= 80 and cash > 0:
            sh += cash / tr[i]
            cash = 0.0
        vals.append(sh * tr[i] + cash)
    v = pd.Series(vals, index=d.index)
    return {"final": float(v.iloc[-1]), "idle_cash_end": float(cash)}


def alloc(df: pd.DataFrame, s: pd.Series, start: str) -> dict:
    d = df[df.index >= start]
    r = d["tr"].pct_change().fillna(0).values
    tb = d["tbill"].ffill().fillna(0).values / 100 / 252
    sv = s.reindex(d.index).ffill().fillna(50).values
    eq = bh = 1.0
    w, month = 1.0, None
    pk_e = pk_b = 1.0
    dd_e = dd_b = 0.0
    for i in range(1, len(d)):
        if d.index[i - 1].month != month:
            month = d.index[i - 1].month
            w = 0.5 + 0.5 * sv[i - 1] / 100
        eq *= 1 + w * r[i] + (1 - w) * tb[i]
        bh *= 1 + r[i]
        pk_e, pk_b = max(pk_e, eq), max(pk_b, bh)
        dd_e, dd_b = min(dd_e, eq / pk_e - 1), min(dd_b, bh / pk_b - 1)
    yrs = (len(d) - 1) / 252
    return {"cagr": eq ** (1 / yrs) - 1, "mdd": dd_e, "bh_cagr": bh ** (1 / yrs) - 1, "bh_mdd": dd_b}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    if not args.no_fetch:
        fetch_all()
    df = build()
    sc = score(df)
    tr = df["tr"]
    ic = ic_table(sc, tr)
    print("IC (สหสัมพันธ์อันดับ คะแนน vs ผลตอบแทนข้างหน้า) — ช่วงทดสอบ ≤2009 | ช่วงยืนยัน ≥2010")
    for k, v in ic.items():
        name = LABELS.get(k, "▶ " + k)
        f = lambda x: f"{x:+.2f}" if x is not None else "  – "
        print(f"  {name:<34} 1ปี {f(v['is_1y'])} | {f(v['oos_1y'])}   3ปี {f(v['is_3y'])} | {f(v['oos_3y'])}")
    zt = {k: zones(sc[k], tr) for k in ("รวม", "VIX อย่างเดียว")}
    print("\nผลตอบแทน S&P 500 รวมปันผลข้างหน้า ตามโซนคะแนนรวม:")
    for z in zt["รวม"]:
        print(f"  {z['zone']:<8} {z['days_pct']:5.1f}% ของวัน · 1 ปี {z['fwd1y'] * 100:+5.1f}% (บวก {z['win1y'] * 100:.0f}%) · 3 ปี {z['fwd3y_ann'] * 100:+5.1f}%/ปี")
    sims = {}
    for start in (SIM_START, SPLIT):
        base = simulate(df, None, "S0", start)["final"]
        for mode, s in (("S1", sc["รวม"]), ("S2", sc["รวม"]), ("S3", sc["VIX อย่างเดียว"])):
            r = simulate(df, s, mode, start)
            sims[f"{mode}@{start[:4]}"] = {"vs_dca": r["final"] / base, **r}
        sims[f"S4@{start[:4]}"] = alloc(df, sc["รวม"], start)
    print("\nกลยุทธ์ (มูลค่าปลายทางเทียบ DCA 100%):")
    for k, v in sims.items():
        if k.startswith("S4"):
            print(f"  {k}: หุ้น/เงินสดตามคะแนน CAGR {v['cagr'] * 100:.1f}% DD {v['mdd'] * 100:.0f}% · ถือหุ้น 100% {v['bh_cagr'] * 100:.1f}% DD {v['bh_mdd'] * 100:.0f}%")
        else:
            print(f"  {k}: {v['vs_dca']:.3f}x")
    now = {k: (None if pd.isna(sc[k].iloc[-1]) else round(float(sc[k].iloc[-1]), 1)) for k in sc.columns}
    print(f"\nคะแนนล่าสุด ({sc.index[-1].date()}): รวม {now['รวม']} · " + " · ".join(f"{g} {now[g]}" for g in GROUPS))
    hist = sc[["รวม"] + list(GROUPS)].resample("ME").last().dropna(how="all")
    OUT.write_text(json.dumps({
        "as_of": str(sc.index[-1].date()), "ic": ic, "zones": zt, "sims": sims, "now": now,
        "labels": LABELS, "groups": GROUPS,
        "history": {"dates": [str(d.date()) for d in hist.index],
                    **{c: [None if pd.isna(x) else round(float(x), 1) for x in hist[c]] for c in hist.columns}},
        "sp500": {"dates": [str(d.date()) for d in hist.index],
                  "tr": [round(float(x), 2) for x in df["tr"].resample("ME").last().reindex(hist.index)]},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT}")


if __name__ == "__main__":
    import urllib.parse  # noqa: F401
    main()
