# -*- coding: utf-8 -*-
"""每日倉位建議器（融合）+ 回測
骨幹: BB-Regime (determine_alloc_bb_regime)  修正: ADX 趨勢狀態 + classify 階段
輸出: 趨勢位置(regime/stage/position) + target% + 建倉訊號 + 動作 + 賣飛/接刀旗標
目標: 非長線(1~2季)、抓區間/趨勢獲利、避免賣飛、避免接刀
"""
import sys, json, math
import pandas as pd
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from rotation_strategy import determine_alloc_bb_regime
from ta_classify import classify
from ta_indicators import calc_indicators

# ---- inline ADX (avoid importing timing_test which runs its own backtest) ----
def rma(x, period):
    out = np.full(len(x), np.nan)
    if len(x) < period: return out
    out[period-1] = np.nanmean(x[:period]); a = 1/period
    for i in range(period, len(x)): out[i] = a*x[i] + (1-a)*out[i-1]
    return out

def adx_wilder(h, l, c, p=14):
    n = len(c); tr = np.zeros(n); pdm = np.zeros(n); mdm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
        up = h[i]-h[i-1]; dn = l[i-1]-l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    atr = rma(tr, p); pdi = 100*rma(pdm, p)/atr; mdi = 100*rma(mdm, p)/atr
    den = np.where((pdi+mdi) == 0, np.nan, pdi+mdi)
    dx = np.nan_to_num(100*np.abs(pdi-mdi)/den)
    return rma(dx, p), np.nan_to_num(pdi), np.nan_to_num(mdi)

# ---------------- data ----------------
def load(tk):
    d = json.load(open(f"D:\\Side_Project\\ETF\\backtest_10y\\{tk}.json", encoding="utf-8"))
    df = pd.DataFrame({"date": d["date"], "open": d["open"], "high": d["high"],
                       "low": d["low"], "close": d["close"], "volume": d["volume"]})
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()

# ---------------- precompute (vectorized, no look-ahead) ----------------
def precompute(df):
    dfi = calc_indicators(df)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    n = len(c)
    adx, pdi, mdi = adx_wilder(h, l, c, 14)
    regime = np.where(np.isnan(adx), "range", np.where(adx >= 20, np.where(pdi > mdi, "up", "down"), "range"))
    swing_hi = pd.Series(h).rolling(20).max().values
    swing_lo = pd.Series(l).rolling(20).min().values
    res = np.maximum(swing_hi, dfi["bb_upper"].values)
    sup = np.minimum(swing_lo, dfi["bb_lower"].values)
    pos = np.clip((c - sup) / (res - sup + 1e-9), 0, 1)
    pos = np.where(np.isnan(pos), 0.5, pos)
    hist = dfi["macd_hist"].values
    macd_slope = np.zeros(n); macd_slope[3:] = hist[3:] - hist[:-3]
    closes = list(c)
    bb_target = np.array([determine_alloc_bb_regime(closes, i) for i in range(n)], dtype=float)
    stage = np.array(["整理"] * n, dtype=object); direction = np.array(["neutral"] * n, dtype=object)
    for i in range(4, n):
        try:
            cls = classify(dfi.iloc[i].to_dict(), dfi.iloc[i-3].to_dict())
            stage[i] = cls["stage"]; direction[i] = cls["direction"]
        except Exception:
            pass
    return dict(dfi=dfi, adx=adx, pdi=pdi, mdi=mdi, regime=regime, pos=pos,
                macd_slope=macd_slope, bb_target=bb_target, stage=stage, direction=direction, c=c)

# ---------------- advisor (per day) ----------------
def advisor_at(pc, i, base_key="pos", adx_mode="down"):
    """base_key: 'pos'→帶內位置直接映射(p*100%, 定案); 'bb'→BB-Regime(舊)。
    adx_mode: 'down'→只 down→0 (定案 E, 最簡); 'full'→+range cap50 +防賣飛 floor (舊)。
    建倉訊號(jin) 用 ADX regime=up 當 gate, 與 base/adx_mode 無關。"""
    regime = pc["regime"][i]; stage = pc["stage"][i]; position = pc["pos"][i]
    bb = pc["bb_target"][i]; ms = pc["macd_slope"][i]
    target = (pc["pos"][i]*100.0) if base_key == "pos" else float(bb); flags = []
    if regime == "down":
        target = 0.0; flags.append("下降趨勢→空手(不接刀)")
    if adx_mode == "full":
        if regime == "range" and target > 50:
            target = 50.0; flags.append("盤整→壓50%")
        if regime == "up" and stage in ("初升", "主升") and position < 0.85 and target < 50:
            target = 50.0; flags.append("上升趨勢中→防賣飛(≥50%)")
    jin = bool(regime == "up" and stage in ("初升", "主升") and position < 0.70 and ms > 0)
    if jin and target >= 50: action = "建倉"
    elif target < 10: action = "離場"
    elif target < 35: action = "減碼"
    elif target < 65: action = "持有"
    else: action = "加碼"
    return dict(regime=regime, stage=stage, position=position, target=target,
                jin_cang=jin, action=action, flags=flags)

# ---------------- backtest (stateful, deadband + cost) ----------------
def backtest(pc, cost=0.001, deadband=30.0, base_key="pos", adx_mode="down"):
    c = pc["c"]; n = len(c)
    ret = np.zeros(n); ret[1:] = c[1:]/c[:-1]-1
    targets = np.array([advisor_at(pc, i, base_key, adx_mode)["target"] for i in range(n)])
    jin = np.array([advisor_at(pc, i, base_key, adx_mode)["jin_cang"] for i in range(n)])
    pos = np.zeros(n); actual = 0.0
    for i in range(1, n):
        t = targets[i-1]/100.0
        if abs(t - actual) >= deadband/100.0: actual = t
        pos[i] = actual
    sret = pos*ret - cost*np.abs(np.diff(np.concatenate([[0], pos])))
    eq = np.cumprod(1+sret)
    return eq, pos, sret, targets, jin

def perf(eq, sret, pos, c):
    years = len(c)/252
    total = eq[-1]-1; cagr = eq[-1]**(1/years)-1 if eq[-1] > 0 else -1
    peak = np.maximum.accumulate(eq); dd = (eq/peak-1).min()
    sr = sret.mean()/(sret.std()+1e-15)*math.sqrt(252)
    trades = int((np.abs(np.diff(np.concatenate([[0], pos]))) >= 0.099).sum())
    return total, cagr, dd, sr, trades, pos.mean()

def bh(c):
    ret = c[1:]/c[:-1]-1; eq = np.concatenate([[1], np.cumprod(1+ret)])
    years = len(c)/252
    cagr = eq[-1]**(1/years)-1
    peak = np.maximum.accumulate(eq); dd = (eq/peak-1).min()
    sr = ret.mean()/(ret.std()+1e-15)*math.sqrt(252)
    return eq[-1]-1, cagr, dd, sr

def fwd_returns(c, jin, idx, q=63):
    out = []
    for i in np.where(jin)[0]:
        if i+q < len(c):
            out.append(c[i+q]/c[i]-1)
    return out

def hold_stats(pos, ret):
    holds = []; in_h = False; start = 0
    for i in range(len(pos)):
        if pos[i] >= 0.5 and not in_h: in_h = True; start = i
        elif pos[i] < 0.5 and in_h:
            in_h = False; holds.append((i-start, ret[start:i+1]))
    if in_h: holds.append((len(pos)-start, ret[start:]))
    res = []
    for dur, r in holds:
        ret_h = (1+r).prod()-1
        res.append((dur, ret_h))
    return res

STOCKS = [("0050", "大盤ETF"), ("2884", "銀行"), ("2330", "台積電"), ("6278", "台燿")]
Q1 = 63; Q2 = 126

def run_all():
    for tk, nm in STOCKS:
        df = load(tk); pc = precompute(df); c = pc["c"]; idx = df.index
        eq, pos, sret, targets, jin = backtest(pc)
        bb_pos = np.zeros(len(c)); actual = 0.0
        ret = np.zeros(len(c)); ret[1:] = c[1:]/c[:-1]-1
        for i in range(1, len(c)):
            t = pc["bb_target"][i-1]/100.0
            if abs(t-actual) >= 0.10: actual = t
            bb_pos[i] = actual
        bbsret = bb_pos*ret - 0.001*np.abs(np.diff(np.concatenate([[0], bb_pos]))); bb_eq = np.cumprod(1+bbsret)
        bt, bc, bdd, bsr = bh(c)
        st, sc, sdd, ssr, trades, tinm = perf(eq, sret, pos, c)
        bt2, bc2, bdd2, bsr2, _, _ = perf(bb_eq, bbsret, bb_pos, c)
        f1 = fwd_returns(c, jin, idx, Q1); f2 = fwd_returns(c, jin, idx, Q2)
        def stat(x):
            if len(x) == 0: return "n/a"
            x = np.array(x)
            return f"avg {x.mean()*100:+.1f}% / med {np.median(x)*100:+.1f}% / win {(x>0).mean()*100:.0f}% (n={len(x)})"
        hs = hold_stats(pos, ret)
        if hs:
            durs = np.array([h[0] for h in hs]); hrets = np.array([h[1] for h in hs])
            hold_str = f"n={len(hs)} / 平均 {durs.mean()/Q1:.1f}季 / 中位 {np.median(durs)/Q1:.1f}季 / 勝率 {(hrets>0).mean()*100:.0f}% / 均益 {hrets.mean()*100:+.1f}%"
        else:
            hold_str = "n/a"
        last = advisor_at(pc, len(c)-1)
        print(f"\n=== {tk} {nm} ===  ({idx[0].date()}~{idx[-1].date()}, n={len(c)})")
        print(f"  {'strategy':12}{'total':>9}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}{'trades':>8}{'in-mkt':>8}")
        print(f"  {'B&H':12}{bt*100:>8.0f}%{bc*100:>7.1f}%{bdd*100:>7.0f}%{bsr:>8.2f}{'-':>8}{'100%':>8}")
        print(f"  {'BB-Regime':12}{bt2*100:>8.0f}%{bc2*100:>7.1f}%{bdd2*100:>7.0f}%{bsr2:>8.2f}{'-':>8}{'':>8}")
        print(f"  {'Fused':12}{st*100:>8.0f}%{sc*100:>7.1f}%{sdd*100:>7.0f}%{ssr:>8.2f}{trades:>8}{tinm*100:>7.0f}%")
        print(f"  建倉訊號 forward:  1Q {stat(f1)}")
        print(f"  {'':12}    2Q {stat(f2)}")
        print(f"  持倉段(>=50%): {hold_str}")
        fl = " | ".join(last["flags"]) if last["flags"] else "-"
        print(f"  【今日】{idx[-1].date()}  regime={last['regime']} stage={last['stage']} pos={last['position']:.2f} "
              f"target={last['target']:.0f}%  action={last['action']}  建倉={'Y' if last['jin_cang'] else 'N'}  [{fl}]")

if __name__ == "__main__":
    run_all()
