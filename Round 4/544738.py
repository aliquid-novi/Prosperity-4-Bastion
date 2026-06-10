"""R4 v12 FINAL — auto-cycle 1~11 결과 (after Cycle 9 KEEP).

Backtest (R4 data):
  Train (3-day worse): 707,109   all: 707,910   none: 707,479   (gap 0.11%)
  Empty log (= R4 final scoring proxy):
    worse: 75,302   all: 75,377   none: 75,302
  Day-by-day train: 294k / 188k / 225k (all positive)
  vs R3 asung_mixed (baseline): train +147k (+26%) / empty log +12.5k (+20%)
  R3 v7 actual log BT/RL ratio: 0.9984 (검증)

Layers (priority order):
  1. PAIR_FUSION: HG-VF spread pair (ENTRY=80, EXIT=20)
  2. HG Binary MR: W=1000, THR=35 (Cycle 8 retune from R3 W=2000/THR=25)
  3. VF Binary MR + cp_vf_shift: W=1000, THR=12 (cp shift ADDED to dev)
     - Mark 67 BUY: K=1.0
     - Mark 49 SELL: K=1.2 (fade)
     - Mark 22 SELL: K=0.8 (fade)
     - CAP=8, decay=0.985
  4. V4000/V4500 Binary Opt MR: VF dev → directional, OPT_BIN_THR=19 (Cycle 7)
  5. PVOD V5000-V5500 directional: ENTRY=72, MAX=70, EXIT=10
  6. TTE-aware (Cycle 5): day_idx tracked via traderData (ts reset detection)
     - tte_eff = max(1, 4 - 0.25 × day_idx)
     - entry × (4/tte_eff)^0.25
  7. Taper (Cycle 2): ts >= 950000 → entry × 1.25
  8. Early-day PVOD relax (Cycle 9): ts < 50000 AND |VF dev| >= 60 → entry × 0.85
  9. V6000/V6500 standing bid=0 (free voucher capture)

Position cap: HG=200, VF=200, voucher=300 (R3 동일)

Auto-cycle progression (v7 → v12):
  v7 (initial):                                            675,732 / 71,742
  v8  (Cycle 2 expiry-aware taper):           +5,054      680,786 / 71,742
  v9  (Cycle 5 TTE-aware voucher):           +10,151      690,937 / 71,742
  v10 (Cycle 7 OPT_BIN_THR 15→19):           +14,180      705,117 / 71,742
  v11 (Cycle 8 HG_BIN_THR/W 25→35/2000→1000): +1,524      706,640 / 75,302  ⭐
  v12 (Cycle 9 early-day PVOD relax):           +469      707,109 / 75,302

Discord PnL distribution (R4 reliable, n=30):
  우리 75.3k = top 15-20% tier
  100k+: top 5% (자주 overfit warning)
  200k+: confirmed overfit

Counterparty data (R4 train, cp_signal_lab.py 447 signals):
  Mark 67 BUY VF: drift +2.24 t=+12.98 @ H=5 (informed, ADD to dev)
  Mark 49 SELL VF: drift -1.82 t=-9.68 @ H=5 (dumb, fade)
  Mark 22 SELL VF: drift -1.55 t=-6.45 @ H=5 (dumb, fade)
  Mark 14: passive maker (drift t<2, skip)
  Mark 38: HG/V4000 noisy (t<2 isolated, skip)
"""
from __future__ import annotations

import json
import math
from statistics import NormalDist
from typing import Dict, List, Optional

from datamodel import OrderDepth, TradingState, Order


# =============================================================================
# Products
# =============================================================================
UNDERLYING = "VELVETFRUIT_EXTRACT"
STATIC_PRODUCT = "HYDROGEL_PACK"

# asung IV-scalp strikes — disabled in this file (ATM_SCALP_ENABLE=False),
# but kept so option_scalping() compiles without change.
SCALP_STRIKES = [5000, 5100, 5200, 5300, 5400, 5500]
SCALP_SYMBOLS = [f"VEV_{k}" for k in SCALP_STRIKES]

# Pair-directional vouchers (mid strikes, user's ratio-trader logic)
PAIR_VOD_STRIKES = [5000, 5100, 5200, 5300, 5400, 5500]
PAIR_VOD_SYMBOLS = [f"VEV_{k}" for k in PAIR_VOD_STRIKES]
PAIR_VOD_LIMIT   = 300

# Deep-OTM vouchers: standing bid at 0
DEEP_OTM_SYMBOLS = ["VEV_6000", "VEV_6500"]
DEEP_OTM_LIMIT   = 300

OPT_VOUCHER_CAP = 300

POS_LIMITS = {
    STATIC_PRODUCT: 200,
    UNDERLYING: 200,
    "VEV_4000": 300,
    "VEV_4500": 300,
    **{s: PAIR_VOD_LIMIT for s in PAIR_VOD_SYMBOLS},
    **{s: DEEP_OTM_LIMIT for s in DEEP_OTM_SYMBOLS},
}


# =============================================================================
# Parameters — asung_best sections
# =============================================================================
HG_FAIR = 10000
HG_WALLMID_ALPHA = 0.7
HG_TAKE_WIDTH = 3
HG_MM_EDGE = 2
HG_CLEAR_WIDTH = 0.5

VF_FAIR = 5257
VF_TAKE_WIDTH = 4
VF_MM_EDGE = 1
VF_CLEAR_WIDTH = 1.5
VF_POSITION_CAP_FRAC = 1.0
VF_WALLMID_ALPHA = 0.7

PAIR_RATIO = 1.9025
PAIR_ENTRY_THR = 50.0
PAIR_MAX_THR = 120.0
PAIR_EXIT_THR = 20.0
PAIR_MIN_PCT = 0.5
PAIR_MAX_PCT = 1.0
PAIR_ENABLE = True
PAIR_VOUCHER_STRIKES = []
PAIR_VOUCHER_FRAC = 1.0

HG_OWN_OFI_K = 0.0
HG_OWN_OFI_LEVELS = 3

POLY_C0 = 0.377117197
POLY_C1 = 0.132124272
POLY_C2 = 19.4005307
POLY_C3 = -6.25093823
POLY_C4 = -17.8556662
POLY_C5 = -734.939515
POLY_SIGMA_MIN = 0.05
POLY_SIGMA_MAX = 2.0

IV_EMA_WINDOW = 500
IV_BLEND_ALPHA = 0.6

K_OBI = -7.5
OBI_LEVELS = 3

MICRO_ALPHA = 0.1

ARB_FILTER_ENABLE = False

VF_INFORMED_SIZE = 9
VF_INFORMED_SHIFT = 1.2
VF_INFORMED_DECAY = 0.995

HG_EXTREME_THR = 3
HG_EXTREME_SHIFT = 1.0
HG_EXTREME_DECAY = 0.95

K_VFHG = -0.3
VFHG_WINDOW = 300

PAIR_DETREND_ENABLE = False
PAIR_DETREND_WINDOW = 30000

V45_MM_ENABLE    = False
V45_TAKE_WIDTH   = 4
V45_MM_EDGE      = 6
V45_CLEAR_WIDTH  = 2.0
V45_USE_VF_SHIFT = 1

HG_TRANSITION_ENABLE = True
HG_TR_SMA_SHORT = 20
HG_TR_SMA_LONG = 500
HG_TR_LAG = 20
HG_TR_SPIKE_THR = 12.0
HG_TR_SHIFT = 8.0
HG_TR_DECAY = 0.95

HG_MR_ENABLE = True
HG_MR_THR    = 10.0
HG_MR_WINDOW = 30
VF_MR_ENABLE = True
VF_MR_THR    = 5.0
VF_MR_WINDOW = 30
MR_ONLY_MODE = True

HG_BIN_THR       = 35.0
HG_BIN_WINDOW    = 1000
HG_BIN_MIN_CROSS = 5.0
VF_BIN_THR       = 12.0
VF_BIN_WINDOW    = 1000
VF_BIN_MIN_CROSS = 5.0

OPT_BIN_ENABLE    = True
OPT_BIN_THR       = 19.0
OPT_BIN_MIN_CROSS = 5.0
OPT_4500_FRAC = 1.0
OPT_4000_FRAC = 1.0

PAIR_FUSION_ENABLE = True
PAIR_FUSION_ENTRY  = 80.0
PAIR_FUSION_EXIT   = 20.0
ATM_SCALP_ENABLE   = False   # replaced by ratio-trader pair logic below
VERTICAL_FUSION_ENABLE = False

DAYS_PER_YEAR = 365
EXPIRY_DAYS = 4   # R4 wiki: VEV TTE=4 days at start of round
DAY = 0

# === R4 NEW: counterparty piggyback (VF) ===
# Validated alpha (R4 deep_analysis):
#   Mark 67 BUY VF: drift +2.24, t=+10.16 @ H=10
#   Mark 49 SELL VF: drift -2.14, t=-7.56 @ H=10 (dumb seller, fade UP)
#   Mark 22 SELL VF: drift -1.56, t=-4.72 @ H=10 (dumb seller, fade UP)
# Empirical sweep on R4 3-day data: ADD shift to dev (NOT subtract).
#   v1l_strong (K=1.0/1.2/0.8, CAP=8): +35k vs R3 v28 baseline.
# Direction explanation: short-term spike from these Marks does NOT mean MR is wrong;
#   our W=1000 EMA captures longer reversion. Adding cp shift to dev triggers MR
#   sooner when these Marks push price further from long-term mean.
CP_VF_ENABLE = True
CP_VF_DECAY = 0.985    # half-life ~46 ticks
CP_VF_K_M67 = 1.0      # Mark 67 BUY -> +K*qty fair shift (will trigger SELL sooner if dev>0)
CP_VF_K_FADE_M49 = 1.2 # Mark 49 SELL: dumb, fade their direction
CP_VF_K_FADE_M22 = 0.8 # Mark 22 SELL: dumb, fade
CP_VF_CAP = 8.0
CP_VF_K_M01 = -0.8  # Mark 01 BUY VF — Ante idea #9 validation

THR_OPEN = 0.1
THR_CLOSE = 0.3
LOW_VEGA_THR_ADJ = 0.5
LOW_VEGA_CUTOFF = 1.0
THEO_NORM_WINDOW = 15
IV_SCALPING_WINDOW = 100
IV_SCALPING_THR = 0.02

CLEANUP_START_TS   = 999999
CLEANUP_UNWIND_FRAC = 0.1

# === Cycle 2: expiry-aware voucher inventory taper ===
# After TAPER_START_TS within a day, raise voucher entry thresholds and
# optionally shrink the max position cap to reduce tail-risk near expiry.
# These params are sweep-able (default = no taper for clean baseline).
TAPER_ENABLE = True
TAPER_START_TS = 950000        # last 10% of a day
TAPER_NEW_ENTRY_MULT = 1.25     # OPT_BIN_THR and PAIR_VOD_ENTRY get multiplied
TAPER_MAX_POS_MULT = 1.0       # MAX position cap multiplier post-taper

# === Cycle 9: Early-day PVOD threshold relaxation ===
# Discord hint: tribesdev/sbmxz "sell at beginning" alpha. PVOD captures via VF dev
# but timing slightly delayed. Relax entry when (ts < EARLY_TS) AND VF dev pointing.
EARLY_PVOD_ENABLE = True
EARLY_PVOD_TS = 50000          # first 10% of day
EARLY_PVOD_MULT = 1.30          # PVOD entry × MULT (lower = enter sooner)
EARLY_PVOD_VF_CONFIRM = 15.0  # variant C: VF dev scale    # require |cur_vf_dev| >= this to relax

# === Cycle 5: TTE-aware voucher logic ===
# Track day_idx via timestamp resets (state.timestamp drops back to 0 → new day).
# tte_eff = max(MIN, EXPIRY_DAYS - DECAY_PER_DAY * day_idx)
# entry_eff = base * (EXPIRY_DAYS / tte_eff) ** ENTRY_POWER (scales UP as TTE shrinks)
# pos_eff = base * (tte_eff / EXPIRY_DAYS) ** POS_POWER (scales DOWN as TTE shrinks)
TTE_AWARE_ENABLE = True
TTE_DECAY_PER_DAY = 0.25
TTE_ENTRY_POWER = 0.25
TTE_POS_POWER = 0.0
TTE_MIN = 1.0
TTE_APPLY_PVOD = True
TTE_APPLY_OPT_BIN = True

# =============================================================================
# Parameters — pair directional for VEV_5000-5500
# =============================================================================
PAIR_VOD_ENTRY   = 15.0  # variant C: VF dev scale (was 72 raw spread)
PAIR_VOD_MAX     = 18.0
PAIR_VOD_EXIT    = 1.0   # variant C: VF dev scale
PAIR_VOD_MIN_PCT = 1.00
PAIR_VOD_MAX_PCT = 1.00


# =============================================================================
# BS + smile  (unchanged from asung_best)
# =============================================================================
_N = NormalDist()

def _d1d2(S, K, T, s):
    v = s * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / v
    return d1, d1 - v

def bs_call(S, K, T, s):
    if T <= 0 or s <= 0:
        return max(0.0, S - K)
    d1, d2 = _d1d2(S, K, T, s)
    return S * _N.cdf(d1) - K * _N.cdf(d2)

def bs_vega(S, K, T, s):
    if T <= 0 or s <= 0:
        return 0.0
    d1, _ = _d1d2(S, K, T, s)
    return S * _N.pdf(d1) * math.sqrt(T)

def smile_iv(S, K, T):
    if T <= 0 or S <= 0 or K <= 0:
        return max(POLY_SIGMA_MIN, POLY_C0)
    m = math.log(K / S)
    sigma = (POLY_C0 + POLY_C1 * m + POLY_C2 * m * m
             + POLY_C3 * T + POLY_C4 * m * T + POLY_C5 * m * m * T)
    return max(POLY_SIGMA_MIN, min(POLY_SIGMA_MAX, sigma))

def implied_vol(S, K, T, price, lo=1e-3, hi=3.0, tol=1e-4, iters=40):
    if T <= 0 or S <= 0 or K <= 0 or price is None:
        return None
    intrinsic = max(0.0, S - K)
    if price < intrinsic - 1e-6 or price > S + 1e-6:
        return None
    f_lo = bs_call(S, K, T, lo) - price
    f_hi = bs_call(S, K, T, hi) - price
    if f_lo > 0 or f_hi < 0:
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        f_mid = bs_call(S, K, T, mid) - price
        if abs(f_mid) < tol:
            return mid
        if f_mid > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)

def tte_of(timestamp):
    day_frac = timestamp // 100 / 10_000
    return max(1e-6, (EXPIRY_DAYS - DAY - day_frac) / DAYS_PER_YEAR)


# =============================================================================
# Helpers  (unchanged from asung_best)
# =============================================================================
def _walls(od):
    if od is None:
        return None, None, None
    bid_wall = min(od.buy_orders) if od.buy_orders else None
    ask_wall = max(od.sell_orders) if od.sell_orders else None
    wall_mid = (bid_wall + ask_wall) / 2 if (bid_wall is not None and ask_wall is not None) else None
    return bid_wall, wall_mid, ask_wall

def _best(od):
    bb = max(od.buy_orders) if od.buy_orders else None
    ba = min(od.sell_orders) if od.sell_orders else None
    return bb, ba

def _clip_buy(pos, lim, q):  return max(0, min(q, lim - pos))
def _clip_sell(pos, lim, q): return max(0, min(q, lim + pos))

def _cap_orders_to_limits(result: Dict[str, List[Order]], positions: dict) -> Dict[str, List[Order]]:
    safe: Dict[str, List[Order]] = {}
    for sym, orders in result.items():
        limit = POS_LIMITS.get(sym)
        if limit is None:
            safe[sym] = orders
            continue

        pos = positions.get(sym, 0)
        buy_left = max(0, limit - pos)
        sell_left = max(0, limit + pos)
        capped: List[Order] = []

        for order in orders:
            qty = int(order.quantity)
            if qty > 0:
                allowed = min(qty, buy_left)
                if allowed > 0:
                    capped.append(Order(sym, int(order.price), allowed))
                    buy_left -= allowed
            elif qty < 0:
                allowed = min(-qty, sell_left)
                if allowed > 0:
                    capped.append(Order(sym, int(order.price), -allowed))
                    sell_left -= allowed

        if capped:
            safe[sym] = capped

    return safe

def ema(old, new, window):
    if old is None:
        return new
    alpha = 2.0 / (window + 1)
    return alpha * new + (1 - alpha) * old


# =============================================================================
# Fixed-fair MM  (unchanged from asung_best)
# =============================================================================
def fixed_fair_mm(symbol, fair, od, pos, limit, take_w, mm_edge, clear_w):
    orders: List[Order] = []
    for ap in sorted(od.sell_orders):
        if ap <= fair - take_w:
            q = _clip_buy(pos, limit, abs(od.sell_orders[ap]))
            if q > 0: orders.append(Order(symbol, ap, q)); pos += q
        else: break
    for bp in sorted(od.buy_orders, reverse=True):
        if bp >= fair + take_w:
            q = _clip_sell(pos, limit, od.buy_orders[bp])
            if q > 0: orders.append(Order(symbol, bp, -q)); pos -= q
        else: break
    if pos > 0:
        for bp in sorted(od.buy_orders, reverse=True):
            if bp >= fair + clear_w:
                q = _clip_sell(pos, limit, min(od.buy_orders[bp], pos))
                if q > 0: orders.append(Order(symbol, bp, -q)); pos -= q
            else: break
    elif pos < 0:
        for ap in sorted(od.sell_orders):
            if ap <= fair - clear_w:
                q = _clip_buy(pos, limit, min(abs(od.sell_orders[ap]), -pos))
                if q > 0: orders.append(Order(symbol, ap, q)); pos += q
            else: break
    mb = int(math.floor(fair - mm_edge))
    ma = int(math.ceil(fair + mm_edge))
    bq = _clip_buy(pos, limit, limit - pos)
    sq = _clip_sell(pos, limit, limit + pos)
    if bq > 0: orders.append(Order(symbol, mb, bq))
    if sq > 0: orders.append(Order(symbol, ma, -sq))
    return orders


# =============================================================================
# Option scalping — kept but ATM_SCALP_ENABLE=False so never called
# =============================================================================
def option_scalping(state: TradingState, td_in: dict, td_out: dict
                    ) -> Dict[str, List[Order]]:
    result: Dict[str, List[Order]] = {}
    ts = state.timestamp
    if ts // 100 < THEO_NORM_WINDOW:
        return result
    u_od = state.order_depths.get(UNDERLYING)
    if u_od is None or not u_od.buy_orders or not u_od.sell_orders:
        return result
    bb_u, ba_u = _best(u_od)
    S_mid = (bb_u + ba_u) / 2
    spread_u = ba_u - bb_u
    if MICRO_ALPHA != 0.0:
        v_bid_top = abs(u_od.buy_orders[bb_u])
        v_ask_top = abs(u_od.sell_orders[ba_u])
        denom_m = v_bid_top + v_ask_top
        if denom_m > 0:
            micro = bb_u * v_ask_top / denom_m + ba_u * v_bid_top / denom_m
            S_base = (1.0 - MICRO_ALPHA) * S_mid + MICRO_ALPHA * micro
        else:
            S_base = S_mid
    else:
        S_base = S_mid
    if K_OBI != 0.0:
        sorted_bids = sorted(u_od.buy_orders.items(), reverse=True)[:OBI_LEVELS]
        sorted_asks = sorted(u_od.sell_orders.items())[:OBI_LEVELS]
        bid_size = sum(abs(v) for _, v in sorted_bids)
        ask_size = sum(abs(v) for _, v in sorted_asks)
        denom = bid_size + ask_size
        obi = (bid_size - ask_size) / denom if denom > 0 else 0.0
        S = S_base + K_OBI * spread_u * obi
    else:
        S = S_base
    T = tte_of(ts)
    arb_ok = {K: True for K in SCALP_STRIKES}
    for sym in SCALP_SYMBOLS:
        od = state.order_depths.get(sym)
        if od is None:
            continue
        _, wall_mid, _ = _walls(od)
        bb, ba = _best(od)
        if wall_mid is None:
            if ba is not None:
                wall_mid = ba - 0.5; bb = ba - 1
            elif bb is not None:
                wall_mid = bb + 0.5; ba = bb + 1
            else:
                continue
        K = int(sym.split("_")[1])
        iv_smile = smile_iv(S, K, T)
        ema_key = f"{sym}_iv"
        iv_ema_prev = td_in.get(ema_key)
        if IV_BLEND_ALPHA < 1.0:
            iv_obs = implied_vol(S, K, T, wall_mid)
            if iv_obs is not None:
                eta = 2.0 / (IV_EMA_WINDOW + 1.0)
                iv_ema_new = iv_obs if iv_ema_prev is None else (1 - eta) * iv_ema_prev + eta * iv_obs
            else:
                iv_ema_new = iv_ema_prev
            td_out[ema_key] = iv_ema_new
            iv_fair = (IV_BLEND_ALPHA * iv_smile
                       + (1.0 - IV_BLEND_ALPHA) * iv_ema_new) if iv_ema_new is not None else iv_smile
        else:
            iv_fair = iv_smile
        theo = bs_call(S, K, T, iv_fair)
        vega = bs_vega(S, K, T, iv_fair)
        theo_diff = wall_mid - theo
        new_mean = ema(td_in.get(f"{sym}_m"), theo_diff, THEO_NORM_WINDOW)
        td_out[f"{sym}_m"] = new_mean
        new_avg = ema(td_in.get(f"{sym}_a"), abs(theo_diff - new_mean), IV_SCALPING_WINDOW)
        td_out[f"{sym}_a"] = new_avg
        if new_avg is None or new_avg < IV_SCALPING_THR:
            pos = state.position.get(sym, 0)
            limit = OPT_VOUCHER_CAP
            if pos > 0 and bb is not None:
                q = _clip_sell(pos, limit, min(od.buy_orders[bb], pos))
                if q > 0:
                    result[sym] = [Order(sym, int(bb), -q)]
            elif pos < 0 and ba is not None:
                q = _clip_buy(pos, limit, min(abs(od.sell_orders[ba]), -pos))
                if q > 0:
                    result[sym] = [Order(sym, int(ba), q)]
            continue
        low_vega = LOW_VEGA_THR_ADJ if vega <= LOW_VEGA_CUTOFF else 0.0
        pos = state.position.get(sym, 0)
        limit = OPT_VOUCHER_CAP
        max_buy  = _clip_buy(pos, limit, limit - pos)
        max_sell = _clip_sell(pos, limit, limit + pos)
        signal_sell = theo_diff - wall_mid + (bb or 0) - new_mean
        signal_buy  = theo_diff - wall_mid + (ba or 0) - new_mean
        orders: List[Order] = []
        if signal_sell >= (THR_OPEN + low_vega) and max_sell > 0 and bb is not None:
            avail = od.buy_orders.get(bb, 0)
            q = min(max_sell, avail)
            if q > 0: orders.append(Order(sym, int(bb), -q))
        elif signal_sell >= THR_CLOSE and pos > 0 and bb is not None:
            avail = od.buy_orders.get(bb, 0)
            q = min(pos, avail, max_sell)
            if q > 0: orders.append(Order(sym, int(bb), -q))
        if signal_buy <= -(THR_OPEN + low_vega) and max_buy > 0 and ba is not None:
            avail = abs(od.sell_orders.get(ba, 0))
            q = min(max_buy, avail)
            if q > 0: orders.append(Order(sym, int(ba), q))
        elif signal_buy <= -THR_CLOSE and pos < 0 and ba is not None:
            avail = abs(od.sell_orders.get(ba, 0))
            q = min(-pos, avail, max_buy)
            if q > 0: orders.append(Order(sym, int(ba), q))
        if orders:
            result[sym] = orders
    return result


# =============================================================================
# Pair helpers  (unchanged from asung_best)
# =============================================================================
def _pair_take_buy(sym, current_pos, target_pos, od, lim):
    need = target_pos - current_pos
    remaining = min(need, lim - current_pos)
    orders = []
    for price in sorted(od.sell_orders):
        if remaining <= 0: break
        vol = min(abs(od.sell_orders[price]), remaining)
        if vol > 0:
            orders.append(Order(sym, price, vol)); remaining -= vol
    return orders

def _pair_take_sell(sym, current_pos, target_pos, od, lim):
    need = current_pos - target_pos
    remaining = min(need, lim + current_pos)
    orders = []
    for price in sorted(od.buy_orders, reverse=True):
        if remaining <= 0: break
        vol = min(abs(od.buy_orders[price]), remaining)
        if vol > 0:
            orders.append(Order(sym, price, -vol)); remaining -= vol
    return orders

def _pair_flatten(sym, current_pos, od, lim):
    if current_pos > 0: return _pair_take_sell(sym, current_pos, 0, od, lim)
    if current_pos < 0: return _pair_take_buy(sym, current_pos, 0, od, lim)
    return []

def _pair_target_pct(dev_mag):
    if dev_mag <= PAIR_ENTRY_THR: return 0.0
    t = (dev_mag - PAIR_ENTRY_THR) / max(1e-9, PAIR_MAX_THR - PAIR_ENTRY_THR)
    return PAIR_MIN_PCT + max(0.0, min(1.0, t)) * (PAIR_MAX_PCT - PAIR_MIN_PCT)

def _pair_vod_target_pct(dev_mag):
    if dev_mag <= PAIR_VOD_ENTRY: return 0.0
    t = (dev_mag - PAIR_VOD_ENTRY) / max(1e-9, PAIR_VOD_MAX - PAIR_VOD_ENTRY)
    return PAIR_VOD_MIN_PCT + max(0.0, min(1.0, t)) * (PAIR_VOD_MAX_PCT - PAIR_VOD_MIN_PCT)


# =============================================================================
# Trader
# =============================================================================
class Trader:
    def run(self, state: TradingState):
        try:
            td_in = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td_in = {}
        td_out: dict = {}

        # Cycle 5: track day_idx via traderData (ts reset detection); env-free for portal compliance.
        # Each day timestamp restarts at 0, so a strictly decreasing ts marks a new day.
        prev_ts = td_in.get("prev_ts", -1)
        day_idx = td_in.get("day_idx", 0)
        if 0 <= state.timestamp < prev_ts:
            day_idx += 1
        td_out["prev_ts"] = state.timestamp
        td_out["day_idx"] = day_idx
        # tte_eff per day, used in scaling factors
        if TTE_AWARE_ENABLE:
            tte_eff = max(TTE_MIN, EXPIRY_DAYS - TTE_DECAY_PER_DAY * day_idx)
            tte_entry_scale = (EXPIRY_DAYS / tte_eff) ** TTE_ENTRY_POWER
            tte_pos_scale = (tte_eff / EXPIRY_DAYS) ** TTE_POS_POWER
        else:
            tte_entry_scale = 1.0
            tte_pos_scale = 1.0

        result: Dict[str, List[Order]] = {}

        hg_od = state.order_depths.get(STATIC_PRODUCT)
        vf_od = state.order_depths.get(UNDERLYING)
        pair_active = False

        # ── HG/VF pair trading (asung fusion layer) ──────────────────────────
        if (PAIR_ENABLE or PAIR_FUSION_ENABLE) and hg_od is not None and vf_od is not None \
           and hg_od.buy_orders and hg_od.sell_orders \
           and vf_od.buy_orders and vf_od.sell_orders:
            hg_mid = (max(hg_od.buy_orders) + min(hg_od.sell_orders)) / 2
            vf_mid = (max(vf_od.buy_orders) + min(vf_od.sell_orders)) / 2
            prev_vf_mid = td_in.get('prev_vf_mid', vf_mid)
            td_out['prev_vf_mid'] = vf_mid
            raw_spread = hg_mid - PAIR_RATIO * prev_vf_mid
            if PAIR_DETREND_ENABLE:
                eta_p = 2.0 / (PAIR_DETREND_WINDOW + 1.0)
                prev_pmean = td_in.get('pair_mean', raw_spread)
                pair_mean = (1 - eta_p) * prev_pmean + eta_p * raw_spread
                td_out['pair_mean'] = pair_mean
                dev = raw_spread - pair_mean
            else:
                dev = raw_spread
            side = td_in.get('pair_side')
            exited = False

            def _flatten_pair_vouchers():
                for K in PAIR_VOUCHER_STRIKES:
                    sym_v = f"VEV_{K}"
                    od_v = state.order_depths.get(sym_v)
                    if od_v is None or not od_v.buy_orders or not od_v.sell_orders:
                        continue
                    pos_v = state.position.get(sym_v, 0)
                    if pos_v != 0:
                        ords = _pair_flatten(sym_v, pos_v, od_v, OPT_VOUCHER_CAP)
                        if ords:
                            result[sym_v] = ords

            _entry = PAIR_FUSION_ENTRY if PAIR_FUSION_ENABLE else PAIR_ENTRY_THR
            _exit  = PAIR_FUSION_EXIT  if PAIR_FUSION_ENABLE else PAIR_EXIT_THR
            if side == 'lv' and dev < _exit:
                hg_pos = state.position.get(STATIC_PRODUCT, 0)
                vf_pos = state.position.get(UNDERLYING, 0)
                result[STATIC_PRODUCT] = _pair_flatten(STATIC_PRODUCT, hg_pos, hg_od, POS_LIMITS[STATIC_PRODUCT])
                result[UNDERLYING]     = _pair_flatten(UNDERLYING, vf_pos, vf_od, POS_LIMITS[UNDERLYING])
                _flatten_pair_vouchers()
                side = None; exited = True; pair_active = True
            elif side == 'sv' and dev > -_exit:
                hg_pos = state.position.get(STATIC_PRODUCT, 0)
                vf_pos = state.position.get(UNDERLYING, 0)
                result[STATIC_PRODUCT] = _pair_flatten(STATIC_PRODUCT, hg_pos, hg_od, POS_LIMITS[STATIC_PRODUCT])
                result[UNDERLYING]     = _pair_flatten(UNDERLYING, vf_pos, vf_od, POS_LIMITS[UNDERLYING])
                _flatten_pair_vouchers()
                side = None; exited = True; pair_active = True
            if not exited:
                if side is None:
                    if dev > _entry: side = 'lv'
                    elif dev < -_entry: side = 'sv'
                if side == 'lv' and dev > _entry:
                    pct = _pair_target_pct(abs(dev))
                    pair_active = True
                    vf_target = int(pct * POS_LIMITS[UNDERLYING])
                    hg_target = -int(pct * POS_LIMITS[STATIC_PRODUCT])
                    vf_pos = state.position.get(UNDERLYING, 0)
                    hg_pos = state.position.get(STATIC_PRODUCT, 0)
                    if vf_pos < vf_target:
                        result[UNDERLYING] = _pair_take_buy(UNDERLYING, vf_pos, vf_target, vf_od, POS_LIMITS[UNDERLYING])
                    if hg_pos > hg_target:
                        result[STATIC_PRODUCT] = _pair_take_sell(STATIC_PRODUCT, hg_pos, hg_target, hg_od, POS_LIMITS[STATIC_PRODUCT])
                elif side == 'sv' and dev < -_entry:
                    pct = _pair_target_pct(abs(dev))
                    pair_active = True
                    vf_target = -int(pct * POS_LIMITS[UNDERLYING])
                    hg_target = int(pct * POS_LIMITS[STATIC_PRODUCT])
                    vf_pos = state.position.get(UNDERLYING, 0)
                    hg_pos = state.position.get(STATIC_PRODUCT, 0)
                    if vf_pos > vf_target:
                        result[UNDERLYING] = _pair_take_sell(UNDERLYING, vf_pos, vf_target, vf_od, POS_LIMITS[UNDERLYING])
                    if hg_pos < hg_target:
                        result[STATIC_PRODUCT] = _pair_take_buy(STATIC_PRODUCT, hg_pos, hg_target, hg_od, POS_LIMITS[STATIC_PRODUCT])
            td_out['pair_side'] = side

        # ── Signals carried through the rest of the tick ──────────────────────
        vf_shift = td_in.get('vf_inf_shift', 0.0) * VF_INFORMED_DECAY
        cp_vf_shift = td_in.get('cp_vf_shift', 0.0) * CP_VF_DECAY
        vf_market = state.market_trades.get(UNDERLYING) if hasattr(state, 'market_trades') else None
        if vf_market and vf_od is not None and vf_od.buy_orders and vf_od.sell_orders:
            best_bid_v = max(vf_od.buy_orders)
            best_ask_v = min(vf_od.sell_orders)
            mid_v = (best_bid_v + best_ask_v) / 2
            for tr in vf_market:
                q = abs(getattr(tr, 'quantity', 0))
                p = getattr(tr, 'price', None)
                buyer = getattr(tr, 'buyer', None)
                seller = getattr(tr, 'seller', None)
                # Legacy informed signal.
                if q >= VF_INFORMED_SIZE and p is not None:
                    sign = 1.0 if p > mid_v else (-1.0 if p < mid_v else 0.0)
                    vf_shift += sign * VF_INFORMED_SHIFT
                # R4 counterparty piggyback (named Marks, validated alpha).
                if CP_VF_ENABLE and p is not None:
                    if buyer == "Mark 67":
                        cp_vf_shift += CP_VF_K_M67 * q
                    if buyer == "Mark 01":
                        cp_vf_shift += CP_VF_K_M01 * q
                    if seller == "Mark 49":
                        cp_vf_shift += CP_VF_K_FADE_M49 * q
                    if seller == "Mark 22":
                        cp_vf_shift += CP_VF_K_FADE_M22 * q
            if cp_vf_shift > CP_VF_CAP: cp_vf_shift = CP_VF_CAP
            elif cp_vf_shift < -CP_VF_CAP: cp_vf_shift = -CP_VF_CAP
        td_out['vf_inf_shift'] = vf_shift
        td_out['cp_vf_shift'] = cp_vf_shift

        vf_flow_buf = td_in.get('vf_flow_buf', [])
        new_flow = 0
        if vf_market and vf_od is not None and vf_od.buy_orders and vf_od.sell_orders:
            best_bid_v = max(vf_od.buy_orders)
            best_ask_v = min(vf_od.sell_orders)
            for tr in vf_market:
                p = getattr(tr, 'price', None)
                q = abs(getattr(tr, 'quantity', 0))
                if p is None or q == 0: continue
                if p >= best_ask_v:   new_flow += q
                elif p <= best_bid_v: new_flow -= q
        vf_flow_buf.append(new_flow)
        if len(vf_flow_buf) > VFHG_WINDOW:
            vf_flow_buf = vf_flow_buf[-VFHG_WINDOW:]
        td_out['vf_flow_buf'] = vf_flow_buf
        hg_vfhg_shift = K_VFHG * sum(vf_flow_buf) if K_VFHG != 0.0 else 0.0

        hg_tr_shift = td_in.get('hg_tr_shift', 0.0) * HG_TR_DECAY
        if HG_TRANSITION_ENABLE and hg_od is not None and hg_od.buy_orders and hg_od.sell_orders:
            hg_mid_now = (max(hg_od.buy_orders) + min(hg_od.sell_orders)) / 2.0
            hg_buf = list(td_in.get('hg_mid_buf', []))
            hg_buf.append(hg_mid_now)
            if len(hg_buf) > HG_TR_LAG:
                hg_buf = hg_buf[-HG_TR_LAG:]
            td_out['hg_mid_buf'] = hg_buf
            eta_s = 2.0 / (HG_TR_SMA_SHORT + 1.0)
            eta_l = 2.0 / (HG_TR_SMA_LONG + 1.0)
            prev_sma_s = td_in.get('hg_sma_s', hg_mid_now)
            prev_sma_l = td_in.get('hg_sma_l', hg_mid_now)
            sma_s = (1 - eta_s) * prev_sma_s + eta_s * hg_mid_now
            sma_l = (1 - eta_l) * prev_sma_l + eta_l * hg_mid_now
            td_out['hg_sma_s'] = sma_s
            td_out['hg_sma_l'] = sma_l
            if len(hg_buf) >= HG_TR_LAG:
                d_lag = hg_mid_now - hg_buf[0]
                if abs(d_lag) > HG_TR_SPIKE_THR:
                    spike_dir = 1.0 if d_lag > 0 else -1.0
                    sma_dir   = 1.0 if sma_s > sma_l else -1.0
                    if spike_dir != sma_dir:
                        hg_tr_shift += spike_dir * HG_TR_SHIFT
        td_out['hg_tr_shift'] = hg_tr_shift

        hg_shift = td_in.get('hg_ext_shift', 0.0) * HG_EXTREME_DECAY
        hg_market = state.market_trades.get(STATIC_PRODUCT) if hasattr(state, 'market_trades') else None
        if hg_market and hg_od is not None and hg_od.buy_orders and hg_od.sell_orders:
            best_bid_h = max(hg_od.buy_orders)
            best_ask_h = min(hg_od.sell_orders)
            mid_h = (best_bid_h + best_ask_h) / 2
            mid_dev = mid_h - 10000.0
            if abs(mid_dev) >= HG_EXTREME_THR:
                for tr in hg_market:
                    p = getattr(tr, 'price', None)
                    if p is None: continue
                    if p >= best_ask_h and mid_dev > 0:
                        hg_shift -= HG_EXTREME_SHIFT
                    elif p <= best_bid_h and mid_dev < 0:
                        hg_shift += HG_EXTREME_SHIFT
        td_out['hg_ext_shift'] = hg_shift

        hg_ofi_shift = 0.0
        if HG_OWN_OFI_K != 0.0 and hg_od is not None and hg_od.buy_orders and hg_od.sell_orders:
            sb = sorted(hg_od.buy_orders.items(), reverse=True)[:HG_OWN_OFI_LEVELS]
            sa = sorted(hg_od.sell_orders.items())[:HG_OWN_OFI_LEVELS]
            bid_sz = sum(abs(v) for _, v in sb)
            ask_sz = sum(abs(v) for _, v in sa)
            denom_h = bid_sz + ask_sz
            if denom_h > 0:
                obi_h = (bid_sz - ask_sz) / denom_h
                bb_h, ba_h = _best(hg_od)
                spread_h = ba_h - bb_h
                hg_ofi_shift = HG_OWN_OFI_K * spread_h * obi_h

        # ── Binary MR for HG and VF ───────────────────────────────────────────
        def _binary_mr(symbol, od, max_pos, ema_window, thr, min_cross, td_key_ema, td_key_dev,
                       fair_shift=0.0):
            if od is None or not od.buy_orders or not od.sell_orders:
                return None, False, None
            bw, wm, aw = _walls(od)
            if wm is None:
                return None, False, None
            eta = 2.0 / (ema_window + 1.0)
            default_init = 10000.0 if symbol == STATIC_PRODUCT else (5257.0 if symbol == UNDERLYING else wm)
            ema_prev = td_in.get(td_key_ema, default_init)
            ema_val = (1 - eta) * ema_prev + eta * wm
            td_out[td_key_ema] = ema_val
            prev_dev = td_in.get(td_key_dev, 0.0)
            # R4 NEW: counterparty fair shift ADDED to dev (empirically +35k vs subtract).
            dev = wm - ema_val + fair_shift
            td_out[td_key_dev] = dev
            pos = state.position.get(symbol, 0)
            bb, ba = _best(od)
            if dev > thr:
                target = -max_pos
                need = pos - target
                if need > 0 and bw is not None:
                    return [Order(symbol, int(bw + 1), -need)], True, dev
            elif dev < -thr:
                target = max_pos
                need = target - pos
                if need > 0 and aw is not None:
                    return [Order(symbol, int(aw - 1), need)], True, dev
            crossed = (prev_dev > 0 and dev < 0) or (prev_dev < 0 and dev > 0)
            if crossed and pos != 0 and abs(prev_dev) >= min_cross:
                if pos > 0 and bb is not None:
                    avail = od.buy_orders.get(bb, 0)
                    q = min(pos, avail)
                    if q > 0:
                        return [Order(symbol, int(bb), -q)], True, dev
                elif pos < 0 and ba is not None:
                    avail = abs(od.sell_orders.get(ba, 0))
                    q = min(-pos, avail)
                    if q > 0:
                        return [Order(symbol, int(ba), q)], True, dev
            return None, False, dev

        hg_mr_active = False
        vf_mr_active = False
        cur_vf_dev = None
        if not pair_active and HG_MR_ENABLE:
            ords, fired, _ = _binary_mr(STATIC_PRODUCT, hg_od, POS_LIMITS[STATIC_PRODUCT],
                                        HG_BIN_WINDOW, HG_BIN_THR, HG_BIN_MIN_CROSS,
                                        'hg_mr_ema', 'hg_prev_dev')
            if ords:
                result[STATIC_PRODUCT] = ords
                hg_mr_active = True

        if not pair_active and VF_MR_ENABLE:
            # Pass cp_vf_shift as fair shift (R4 alpha layer).
            ords, fired, dev = _binary_mr(UNDERLYING, vf_od, POS_LIMITS[UNDERLYING],
                                          VF_BIN_WINDOW, VF_BIN_THR, VF_BIN_MIN_CROSS,
                                          'vf_mr_ema', 'vf_prev_dev',
                                          fair_shift=cp_vf_shift)
            if ords:
                result[UNDERLYING] = ords
                vf_mr_active = True
            cur_vf_dev = dev

        # ── OPT_BIN: VEV_4000 + VEV_4500 driven by VF dev ───────────────────
        # Cycle 2 NEW: expiry-aware taper after TAPER_START_TS within day
        in_taper = TAPER_ENABLE and state.timestamp >= TAPER_START_TS
        opt_thr_eff = OPT_BIN_THR * TAPER_NEW_ENTRY_MULT if in_taper else OPT_BIN_THR
        # Cycle 5: TTE-aware scaling
        if TTE_AWARE_ENABLE and TTE_APPLY_OPT_BIN:
            opt_thr_eff *= tte_entry_scale
        if OPT_BIN_ENABLE and cur_vf_dev is not None:
            opt_prev_dev = td_in.get('vf_prev_dev', 0.0)
            for sym, K, frac in [('VEV_4500', 4500, OPT_4500_FRAC),
                                  ('VEV_4000', 4000, OPT_4000_FRAC)]:
                if frac <= 0:
                    continue
                od = state.order_depths.get(sym)
                if od is None or not od.buy_orders or not od.sell_orders:
                    continue
                bb = max(od.buy_orders); ba = min(od.sell_orders)
                bw_o = min(od.buy_orders); aw_o = max(od.sell_orders)
                cur_pos = state.position.get(sym, 0)
                cap_mult = TAPER_MAX_POS_MULT if in_taper else 1.0
                opt_max = int(OPT_VOUCHER_CAP * frac * cap_mult)
                opt_orders: List[Order] = []
                if cur_vf_dev > opt_thr_eff:
                    target = -opt_max
                    need = cur_pos - target
                    if need > 0:
                        opt_orders.append(Order(sym, int(bw_o + 1), -need))
                elif cur_vf_dev < -opt_thr_eff:
                    target = opt_max
                    need = target - cur_pos
                    if need > 0:
                        opt_orders.append(Order(sym, int(aw_o - 1), need))
                else:
                    crossed = (opt_prev_dev > 0 and cur_vf_dev < 0) or (opt_prev_dev < 0 and cur_vf_dev > 0)
                    if crossed and cur_pos != 0 and abs(opt_prev_dev) >= OPT_BIN_MIN_CROSS:
                        if cur_pos > 0:
                            avail = od.buy_orders.get(bb, 0)
                            q = min(cur_pos, avail)
                            if q > 0:
                                opt_orders.append(Order(sym, int(bb), -q))
                        else:
                            avail = abs(od.sell_orders.get(ba, 0))
                            q = min(-cur_pos, avail)
                            if q > 0:
                                opt_orders.append(Order(sym, int(ba), q))
                if opt_orders:
                    result[sym] = opt_orders

        # ── Default MMs (MR_ONLY_MODE=True → skipped, kept for reference) ────
        if not MR_ONLY_MODE and not pair_active and not hg_mr_active and hg_od is not None and hg_od.buy_orders and hg_od.sell_orders:
            _, hg_wm, _ = _walls(hg_od)
            hg_fair = HG_FAIR if hg_wm is None else (HG_WALLMID_ALPHA * hg_wm + (1 - HG_WALLMID_ALPHA) * HG_FAIR)
            result[STATIC_PRODUCT] = fixed_fair_mm(
                STATIC_PRODUCT, hg_fair + hg_shift + hg_vfhg_shift + hg_tr_shift + hg_ofi_shift,
                hg_od, state.position.get(STATIC_PRODUCT, 0),
                POS_LIMITS[STATIC_PRODUCT], HG_TAKE_WIDTH, HG_MM_EDGE, HG_CLEAR_WIDTH)

        if not MR_ONLY_MODE and not pair_active and not vf_mr_active and vf_od is not None and vf_od.buy_orders and vf_od.sell_orders:
            limit = int(POS_LIMITS[UNDERLYING] * VF_POSITION_CAP_FRAC)
            bw, wm, aw = _walls(vf_od)
            fair = VF_FAIR if wm is None else (VF_WALLMID_ALPHA * wm + (1 - VF_WALLMID_ALPHA) * VF_FAIR)
            fair += vf_shift
            result[UNDERLYING] = fixed_fair_mm(
                UNDERLYING, fair, vf_od,
                state.position.get(UNDERLYING, 0), limit,
                VF_TAKE_WIDTH, VF_MM_EDGE, VF_CLEAR_WIDTH)

        # ATM_SCALP_ENABLE=False — IV scalp for 5000-5500 replaced by pair logic below
        if ATM_SCALP_ENABLE:
            scalp_orders = option_scalping(state, td_in, td_out)
            for _sym, _orders in scalp_orders.items():
                if _sym not in ('VEV_4000', 'VEV_4500'):
                    result.setdefault(_sym, []).extend(_orders)

        # ── Pair directional: VEV_5000-5500 (user's ratio-trader logic) ───────
        # Uses HG/VF spread signal; only trades the 6 mid-strike vouchers.
        if hg_od is not None and vf_od is not None \
           and hg_od.buy_orders and hg_od.sell_orders \
           and vf_od.buy_orders and vf_od.sell_orders:
            hg_mid_v = (max(hg_od.buy_orders) + min(hg_od.sell_orders)) / 2
            vf_mid_v = (max(vf_od.buy_orders) + min(vf_od.sell_orders)) / 2
            # Variant C: use VF binary MR dev (cur_vf_dev) as signal instead of raw HG-VF spread.
            # When VF too high (dev>0), expect VF to revert down → vouchers SELL (sv-side).
            # Side flip vs spread version: spread up = VF down (with HG noise), so old 'lv' (spread>+entry)
            # corresponds to new 'sv' (vf dev<-entry). Below we use vf-aligned semantics directly:
            # dev_v positive → VF too high → SELL voucher (sv); dev_v negative → BUY (lv).
            # Equivalent: keep old 'lv'/'sv' but flip sign so semantics match (lv = voucher BUY = VF too low).
            dev_v = -(cur_vf_dev if cur_vf_dev is not None else 0.0)
            pvod_side = td_in.get('pvod_side')
            pvod_exited = False

            if pvod_side == 'lv' and dev_v < PAIR_VOD_EXIT:
                for sym in PAIR_VOD_SYMBOLS:
                    pos_v = state.position.get(sym, 0)
                    od_v = state.order_depths.get(sym, OrderDepth())
                    ords = _pair_flatten(sym, pos_v, od_v, PAIR_VOD_LIMIT)
                    if ords: result[sym] = ords
                pvod_side = None; pvod_exited = True
            elif pvod_side == 'sv' and dev_v > -PAIR_VOD_EXIT:
                for sym in PAIR_VOD_SYMBOLS:
                    pos_v = state.position.get(sym, 0)
                    od_v = state.order_depths.get(sym, OrderDepth())
                    ords = _pair_flatten(sym, pos_v, od_v, PAIR_VOD_LIMIT)
                    if ords: result[sym] = ords
                pvod_side = None; pvod_exited = True

            if not pvod_exited:
                # Cycle 2: taper PVOD entry threshold post-TAPER_START_TS
                pvod_entry_eff = PAIR_VOD_ENTRY * TAPER_NEW_ENTRY_MULT if in_taper else PAIR_VOD_ENTRY
                if TTE_AWARE_ENABLE and TTE_APPLY_PVOD:
                    pvod_entry_eff *= tte_entry_scale
                # Cycle 9: early-day relaxation
                if (EARLY_PVOD_ENABLE and state.timestamp < EARLY_PVOD_TS
                    and abs(dev_v) >= EARLY_PVOD_VF_CONFIRM):
                    pvod_entry_eff *= EARLY_PVOD_MULT
                if pvod_side is None:
                    if dev_v > pvod_entry_eff:   pvod_side = 'lv'
                    elif dev_v < -pvod_entry_eff: pvod_side = 'sv'
                tgt_pct = _pair_vod_target_pct(abs(dev_v))
                # Apply MAX_POS_MULT to PVOD limit too
                pos_mult = TAPER_MAX_POS_MULT if in_taper else 1.0
                if TTE_AWARE_ENABLE and TTE_APPLY_PVOD:
                    pos_mult *= tte_pos_scale
                pvod_limit_eff = int(PAIR_VOD_LIMIT * pos_mult)
                if pvod_side == 'lv' and dev_v > pvod_entry_eff and tgt_pct > 0:
                    for sym in PAIR_VOD_SYMBOLS:
                        target_pos = int(tgt_pct * pvod_limit_eff)
                        cur_pos = state.position.get(sym, 0)
                        if cur_pos < target_pos:
                            od_v = state.order_depths.get(sym)
                            if od_v and od_v.sell_orders:
                                ords = _pair_take_buy(sym, cur_pos, target_pos, od_v, PAIR_VOD_LIMIT)
                                if ords: result[sym] = ords
                elif pvod_side == 'sv' and dev_v < -pvod_entry_eff and tgt_pct > 0:
                    for sym in PAIR_VOD_SYMBOLS:
                        target_pos = -int(tgt_pct * pvod_limit_eff)
                        cur_pos = state.position.get(sym, 0)
                        if cur_pos > target_pos:
                            od_v = state.order_depths.get(sym)
                            if od_v and od_v.buy_orders:
                                ords = _pair_take_sell(sym, cur_pos, target_pos, od_v, PAIR_VOD_LIMIT)
                                if ords: result[sym] = ords

            if pvod_side is not None:
                td_out['pvod_side'] = pvod_side

        # ── Deep OTM: VEV_6000, VEV_6500 — standing bid at 0 ─────────────────
        for sym in DEEP_OTM_SYMBOLS:
            pos = state.position.get(sym, 0)
            qty = DEEP_OTM_LIMIT - pos
            if qty > 0:
                result[sym] = [Order(sym, 0, qty)]

        try:
            trader_data = json.dumps(td_out, ensure_ascii=False)
        except Exception:
            trader_data = state.traderData or ""

        result = _cap_orders_to_limits(result, state.position)
        return result, 0, trader_data