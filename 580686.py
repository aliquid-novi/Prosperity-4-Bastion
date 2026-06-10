"""
R5 v3 base strategy + Galaxy overlay + Planetary Rings trend-regime filter
+ selected optimal position-ignorant MM overrides.

Purpose:
  - Keep teammate base strategy intact:
      * EMA/std opportunistic takes
      * standing MM at best_bid+1 / best_ask-1
      * position limit 10
  - Keep Galaxy lead-lag overlay.
  - Add a product-specific trend gate for GALAXY_SOUNDS_PLANETARY_RINGS,
    because the website run showed PR was the main drag when re-enabled.

Core idea for Planetary Rings:
  - If PR is downtrending:
      suppress PR buy-side MM
      allow PR sell-side MM
      flatten PR longs faster
  - If PR is uptrending:
      allow PR buy-side MM
      suppress/reduce PR sell-side MM
  - If PR is sideways:
      tiny two-sided PR MM only

This lets you test whether PR can contribute without repeatedly catching
a falling knife.

Optimal MM override:
  - For selected products, skip all other paje logic.
  - Post max buy room at best_bid + 1.
  - Post max sell room at best_ask - 1.
  - This is intentionally position-ignorant beyond respecting the ±10 limit:
    no fair value, no inventory skew, no EMA take, no Galaxy overlay.

Chocolate/Vanilla hybrid:
  - Keep Chocolate on its original paje logic.
  - Replace only Vanilla with a Chocolate-Vanilla spread mean-reversion signal.
  - Spread = Chocolate - Vanilla.
  - Low spread z => Vanilla is rich relative to Chocolate, short Vanilla.
  - High spread z => Vanilla is cheap relative to Chocolate, long Vanilla.

Generic discrete jump "beast mode":
  - If a product's mid stays exactly unchanged for 4 consecutive ticks, arm
    the detector, but do not suppress normal logic yet.
  - If an armed product then jumps by more than 30 in one tick, enter beast
    mode and prioritize only this jump strategy for that product.
  - If mid jumps up by more than 30 in one tick, target -10 at best bid.
  - If mid drops by more than 30 in one tick, target +10 at best ask.
  - While active, true rectangular jumps around 90+ reset the mode-health
    counter. Ordinary level changes above 7 but below that true-jump threshold
    increment it.
  - If 5 such ordinary changes happen without a true rectangular jump, flatten
    to 0 and leave beast mode before waiting for loss-streak exit.
  - After 3 consecutive losing round trips, flatten to 0 and disable this mode
    until a fresh unchanged-mid streak appears.

Single-product mean reversion overrides:
  - PANEL_1X2, PANEL_4X4, ROBOT_VACUUMING, and UV_VISOR_MAGENTA use their
    own mid-vs-EMA z-score signal.
  - Low z => buy/long to +10 at best ask.
  - High z => sell/short to -10 at best bid.
  - Exit back to flat on the configured opposite z threshold.
"""

from __future__ import annotations

import json
import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order


ALL_PRODUCTS = [
    "GALAXY_SOUNDS_DARK_MATTER","GALAXY_SOUNDS_BLACK_HOLES","GALAXY_SOUNDS_PLANETARY_RINGS",
    "GALAXY_SOUNDS_SOLAR_WINDS","GALAXY_SOUNDS_SOLAR_FLAMES",
    "SLEEP_POD_SUEDE","SLEEP_POD_LAMB_WOOL","SLEEP_POD_POLYESTER","SLEEP_POD_NYLON","SLEEP_POD_COTTON",
    "MICROCHIP_CIRCLE","MICROCHIP_OVAL","MICROCHIP_SQUARE","MICROCHIP_RECTANGLE","MICROCHIP_TRIANGLE",
    "PEBBLES_XS","PEBBLES_S","PEBBLES_M","PEBBLES_L","PEBBLES_XL",
    "ROBOT_VACUUMING","ROBOT_MOPPING","ROBOT_DISHES","ROBOT_LAUNDRY","ROBOT_IRONING",
    "UV_VISOR_YELLOW","UV_VISOR_AMBER","UV_VISOR_ORANGE","UV_VISOR_RED","UV_VISOR_MAGENTA",
    "TRANSLATOR_SPACE_GRAY","TRANSLATOR_ASTRO_BLACK","TRANSLATOR_ECLIPSE_CHARCOAL",
    "TRANSLATOR_GRAPHITE_MIST","TRANSLATOR_VOID_BLUE",
    "PANEL_1X2","PANEL_2X2","PANEL_1X4","PANEL_2X4","PANEL_4X4",
    "OXYGEN_SHAKE_MORNING_BREATH","OXYGEN_SHAKE_EVENING_BREATH","OXYGEN_SHAKE_MINT",
    "OXYGEN_SHAKE_CHOCOLATE","OXYGEN_SHAKE_GARLIC",
    "SNACKPACK_CHOCOLATE","SNACKPACK_VANILLA","SNACKPACK_PISTACHIO",
    "SNACKPACK_STRAWBERRY","SNACKPACK_RASPBERRY",
]

POS_LIMIT = 10

PAIR_A = "SNACKPACK_CHOCOLATE"
PAIR_B = "SNACKPACK_VANILLA"

VANILLA_SPREAD_ENABLE = True
VANILLA_SPREAD_ADD_MM = False

VANILLA_SPREAD_HEDGE = 1.0
VANILLA_SPREAD_EMA_W = 850
VANILLA_SPREAD_Z_W = 1750
VANILLA_SPREAD_BUY_Z = -2.5
VANILLA_SPREAD_SELL_Z = 2.5
VANILLA_SPREAD_EXIT_Z = 0.1
VANILLA_SPREAD_MAX_POS = 10
VANILLA_SPREAD_MIN_Z_OBS = max(5, VANILLA_SPREAD_Z_W // 5)

BEAST_MODE_ENABLE = True
BEAST_MODE_FLAT_STREAK_TO_ARM = 6
BEAST_MODE_JUMP_EDGE = 30.0
BEAST_MODE_TRUE_JUMP_EDGE = 45.0
BEAST_MODE_LEVEL_CHANGE_EDGE = 7.0
BEAST_MODE_LEVEL_CHANGES_TO_EXIT = 5
BEAST_MODE_MAX_LOSS_STREAK = 3

SINGLE_MR_ENABLE = True
SINGLE_MR_PRODUCTS = {
    "PANEL_1X2": {
        "ema": 1250,
        "z_window": 1000,
        "buy_z": -2.70,
        "sell_z": 2.70,
        "exit_long_at_z": 0.30,
        "exit_short_at_z": -0.30,
        "max_pos": 10,
        "min_obs": 250,
    },
    "PANEL_4X4": {
        "ema": 800,
        "z_window": 1900,
        "buy_z": -2.60,
        "sell_z": 2.60,
        "exit_long_at_z": -0.30,
        "exit_short_at_z": 0.30,
        "max_pos": 10,
        "min_obs": 380,
    },
    "ROBOT_VACUUMING": {
        "ema": 1500,
        "z_window": 500,
        "buy_z": -3.25,
        "sell_z": 3.25,
        "exit_long_at_z": -0.20,
        "exit_short_at_z": 0.20,
        "max_signal_z": 3.0,
        "min_pct": 1.0,
        "max_pct": 1.0,
        "max_pos": 10,
        "min_obs": 100,
    },
    "UV_VISOR_MAGENTA": {
        "ema": 2000,
        "z_window": 1400,
        "buy_z": -2.95,
        "sell_z": 2.95,
        "exit_long_at_z": 0.10,
        "exit_short_at_z": -0.10,
        "max_pos": 10,
        "min_obs": 350,
    },
}

OPTIMAL_MM_PRODUCTS = {
    # Paje-zero products where pure MM was positive.
    "PANEL_1X4",
    "UV_VISOR_AMBER",
    "OXYGEN_SHAKE_MORNING_BREATH",
    "SLEEP_POD_COTTON",
    "PANEL_2X2",

    # Paje-positive products where pure MM beat paje in the data capsule.
    "PEBBLES_S",
    "ROBOT_IRONING",
    "MICROCHIP_CIRCLE",
    "MICROCHIP_OVAL",
    "GALAXY_SOUNDS_SOLAR_WINDS",
    "OXYGEN_SHAKE_EVENING_BREATH",
    "GALAXY_SOUNDS_BLACK_HOLES",
    "PANEL_2X4",
}

OPTIMAL_MM_EDGE = 1

EXCLUDE_PRODUCTS = {
    "SLEEP_POD_LAMB_WOOL",
    "PANEL_1X4",
    "ROBOT_MOPPING",
    "ROBOT_VACUUMING",
    "GALAXY_SOUNDS_SOLAR_FLAMES",
    "SLEEP_POD_COTTON",
    "GALAXY_SOUNDS_PLANETARY_RINGS",
    "OXYGEN_SHAKE_MORNING_BREATH",
    "PANEL_1X2",
    "PANEL_2X2",
    "UV_VISOR_AMBER",
}

# v18b: momentum trend-follow strategy for clearly-momentum products
# (cdev > +0.005) that paje base has marked as untraded.
# Direction: when mid > EMA + K*std, BUY at ask (price will keep rising).
# When mid < EMA - K*std, SELL at bid. Crossed take, no MM, no inventory skew.
TREND_FOLLOW_PRODUCTS = {
    "ROBOT_MOPPING",         # cdev=+0.012
    "SLEEP_POD_LAMB_WOOL",   # cdev=+0.010
}
TREND_FOLLOW_K = 1.2
TREND_FOLLOW_MIN_EDGE = 200



# ============================================================
# BASE STRATEGY PARAMS
# ============================================================

MM_QTY = 5
MM_EDGE = 1
MM_SKEW_THR = 100

TAKE_ENABLE = True
TAKE_EMA_W = 1000
TAKE_K = 1.30

TAKE_K_OVERRIDE = {
    "PEBBLES_XL": 1.5, "MICROCHIP_TRIANGLE": 1.5, "PEBBLES_S": 1.5,
    "PEBBLES_M": 1.5, "MICROCHIP_RECTANGLE": 1.5, "OXYGEN_SHAKE_CHOCOLATE": 1.5,
    "SNACKPACK_RASPBERRY": 1.5, "ROBOT_DISHES": 1.5, "SLEEP_POD_NYLON": 1.5,
    "PEBBLES_XS": 2.5, "ROBOT_VACUUMING": 2.5,
    "UV_VISOR_YELLOW": 2.5, "PANEL_4X4": 2.5, "UV_VISOR_MAGENTA": 2.5,
    "GALAXY_SOUNDS_BLACK_HOLES": 2.5,
}

TAKE_MIN_EDGE = 200


# ============================================================
# GALAXY OVERLAY CONFIG
# ============================================================

GALAXY_PRODUCTS = [
    "GALAXY_SOUNDS_DARK_MATTER",
    "GALAXY_SOUNDS_BLACK_HOLES",
    "GALAXY_SOUNDS_PLANETARY_RINGS",
    "GALAXY_SOUNDS_SOLAR_WINDS",
    "GALAXY_SOUNDS_SOLAR_FLAMES",
]

GALAXY_SIGNAL_A = "GALAXY_SOUNDS_BLACK_HOLES"
GALAXY_SIGNAL_B = "GALAXY_SOUNDS_PLANETARY_RINGS"

GALAXY_NOT_TRADED_PRODUCTS = {
    "GALAXY_SOUNDS_PLANETARY_RINGS",
    "GALAXY_SOUNDS_SOLAR_FLAMES",
}

GALAXY_OVERLAY_ENABLE = True

# Main test flag:
#   True  -> overlay can affect all Galaxy products.
#   False -> use GALAXY_TRADE_ONLY_NOT_TRADED_PRODUCTS below.
GALAXY_TRADE_ENTIRE_BASKET = True

# Only used when GALAXY_TRADE_ENTIRE_BASKET = False.
GALAXY_TRADE_ONLY_NOT_TRADED_PRODUCTS = True

# Excluded Galaxy products are risky under base EMA-MR.
# Let overlay quote them, but do not let base EMA-taking re-enable them.
GALAXY_ALLOW_BASE_TAKE_ON_EXCLUDED_TARGETS = False

# Excluded products only quote when signal is active.
GALAXY_REQUIRE_SIGNAL_FOR_EXCLUDED_TARGETS = True

# Signal:
#   spread = BLACK_HOLES - PLANETARY_RINGS
#   spread_ma_bias = fast_ema(spread) - slow_ema(spread)
# Evidence from your tests:
#   high spread_ma_bias -> future Galaxy basket ex-pair tends DOWN
# Therefore:
#   score = -z(spread_ma_bias)
#   score > 0 => bullish/buy-side bias
#   score < 0 => bearish/sell-side bias
GALAXY_FAST_W = 200
GALAXY_SLOW_W = 1000
GALAXY_SIGNAL_VAR_W = 1000

GALAXY_SCORE_THRESHOLD = 0.05
GALAXY_STRONG_THRESHOLD = 0.75
GALAXY_EXTREME_THRESHOLD = 1.50

# Sizes for already-traded Galaxy products.
GALAXY_NEUTRAL_QTY = 5
GALAXY_LIGHT_FAV_QTY = 5
GALAXY_LIGHT_UNFAV_QTY = 4
GALAXY_STRONG_FAV_QTY = 5
GALAXY_STRONG_UNFAV_QTY = 3
GALAXY_EXTREME_FAV_QTY = 5
GALAXY_EXTREME_UNFAV_QTY = 2

# Sizes for excluded/re-enabled Galaxy products.
GALAXY_EXCLUDED_NEUTRAL_QTY = 0
GALAXY_EXCLUDED_LIGHT_FAV_QTY = 2
GALAXY_EXCLUDED_LIGHT_UNFAV_QTY = 1
GALAXY_EXCLUDED_STRONG_FAV_QTY = 3
GALAXY_EXCLUDED_STRONG_UNFAV_QTY = 1
GALAXY_EXCLUDED_EXTREME_FAV_QTY = 3
GALAXY_EXCLUDED_EXTREME_UNFAV_QTY = 1

# Price improvement for fill rate.
GALAXY_PRICE_IMPROVE_ENABLE = True
GALAXY_PRICE_IMPROVE_THRESHOLD = 0.75
GALAXY_PRICE_IMPROVE_MIN_SPREAD = 4
GALAXY_EXTRA_TICK = 1

GALAXY_SUPPRESS_ADVERSE_SIDE = False
GALAXY_EXCLUDED_PRICE_IMPROVE_THRESHOLD = 1.00

GALAXY_DEBUG = False
GALAXY_PRINT_EVERY = 500


# ============================================================
# PLANETARY RINGS TREND REGIME FILTER
# ============================================================

PR = "GALAXY_SOUNDS_PLANETARY_RINGS"

PR_TREND_FILTER_ENABLE = True

# Trend windows for PR.
PR_FAST_W = 100
PR_SLOW_W = 600
PR_TREND_VAR_W = 600

# Regime thresholds.
# trend_z = zscore(fast_ema(PR_mid) - slow_ema(PR_mid))
PR_DOWNTREND_Z = -0.80
PR_UPTREND_Z = 0.80

# Regime behaviour.
PR_SUPPRESS_BUYS_IN_DOWNTREND = True
PR_SUPPRESS_SELLS_IN_UPTREND = True

# Flatten long PR in downtrend.
PR_FLATTEN_LONGS_IN_DOWNTREND = True
PR_FLATTEN_LONG_TAKE_QTY = 3

# Conservative PR sizes.
PR_SIDEWAYS_QTY = 1
PR_TREND_FAV_QTY = 2
PR_TREND_UNFAV_QTY = 0

PR_DEBUG = False
PR_PRINT_EVERY = 500


class Trader:
    def __init__(self):
        self._vanilla_residuals: List[float] = []
        self._vanilla_residual_sum = 0.0
        self._vanilla_residual_sumsq = 0.0
        self._single_mr_residuals: Dict[str, List[float]] = {}
        self._single_mr_residual_sum: Dict[str, float] = {}
        self._single_mr_residual_sumsq: Dict[str, float] = {}

    def _get_best_mid(self, state: TradingState, sym: str):
        od = state.order_depths.get(sym)
        if od is None or not od.buy_orders or not od.sell_orders:
            return None
        bb = max(od.buy_orders)
        ba = min(od.sell_orders)
        return (bb + ba) / 2.0

    def _galaxy_targets(self):
        if not GALAXY_OVERLAY_ENABLE:
            return set()
        if GALAXY_TRADE_ENTIRE_BASKET:
            return set(GALAXY_PRODUCTS)
        if GALAXY_TRADE_ONLY_NOT_TRADED_PRODUCTS:
            return set(GALAXY_NOT_TRADED_PRODUCTS)
        return set()

    def _update_galaxy_signal(self, state: TradingState, td_in: dict):
        g_in = td_in.get("galaxy", {})
        tick = int(g_in.get("tick", 0))

        mid_a = self._get_best_mid(state, GALAXY_SIGNAL_A)
        mid_b = self._get_best_mid(state, GALAXY_SIGNAL_B)

        if mid_a is None or mid_b is None:
            g_out = dict(g_in)
            g_out["tick"] = tick + 1
            return g_out, 0.0, False

        spread = mid_a - mid_b

        alpha_fast = 2.0 / (GALAXY_FAST_W + 1.0)
        alpha_slow = 2.0 / (GALAXY_SLOW_W + 1.0)
        alpha_var = 2.0 / (GALAXY_SIGNAL_VAR_W + 1.0)

        prev_fast = g_in.get("fast", spread)
        prev_slow = g_in.get("slow", spread)

        fast = (1.0 - alpha_fast) * prev_fast + alpha_fast * spread
        slow = (1.0 - alpha_slow) * prev_slow + alpha_slow * spread

        spread_ma_bias = fast - slow

        prev_signal_var = g_in.get("signal_var", 1.0)
        signal_var = (1.0 - alpha_var) * prev_signal_var + alpha_var * (spread_ma_bias * spread_ma_bias)
        signal_std = max(1.0, math.sqrt(signal_var))

        raw_z = spread_ma_bias / signal_std

        # Invert because high spread_ma_bias predicted basket down.
        galaxy_score = -raw_z
        galaxy_score = max(-2.0, min(2.0, galaxy_score))

        signal_active = abs(galaxy_score) >= GALAXY_SCORE_THRESHOLD

        g_out = {
            "tick": tick + 1,
            "spread": spread,
            "fast": fast,
            "slow": slow,
            "spread_ma_bias": spread_ma_bias,
            "signal_var": signal_var,
            "raw_z": raw_z,
            "score": galaxy_score,
        }

        if GALAXY_DEBUG and tick % GALAXY_PRINT_EVERY == 0:
            print(
                "[GALAXY] "
                f"tick={tick} spread={spread:.2f} "
                f"ma_bias={spread_ma_bias:.2f} raw_z={raw_z:.2f} "
                f"score={galaxy_score:.2f} active={signal_active}"
            )

        return g_out, galaxy_score, signal_active

    def _update_planetary_regime(self, state: TradingState, td_in: dict):
        """
        Returns:
            pr_state_out, pr_trend_z, pr_regime

        pr_regime:
            "downtrend"
            "uptrend"
            "sideways"
        """
        pr_in = td_in.get("planetary_regime", {})
        tick = int(pr_in.get("tick", 0))

        mid = self._get_best_mid(state, PR)

        if mid is None:
            pr_out = dict(pr_in)
            pr_out["tick"] = tick + 1
            return pr_out, 0.0, "sideways"

        alpha_fast = 2.0 / (PR_FAST_W + 1.0)
        alpha_slow = 2.0 / (PR_SLOW_W + 1.0)
        alpha_var = 2.0 / (PR_TREND_VAR_W + 1.0)

        prev_fast = pr_in.get("fast", mid)
        prev_slow = pr_in.get("slow", mid)

        fast = (1.0 - alpha_fast) * prev_fast + alpha_fast * mid
        slow = (1.0 - alpha_slow) * prev_slow + alpha_slow * mid

        trend_raw = fast - slow

        prev_var = pr_in.get("trend_var", 1.0)
        trend_var = (1.0 - alpha_var) * prev_var + alpha_var * (trend_raw * trend_raw)
        trend_std = max(1.0, math.sqrt(trend_var))

        trend_z = trend_raw / trend_std

        if trend_z <= PR_DOWNTREND_Z:
            regime = "downtrend"
        elif trend_z >= PR_UPTREND_Z:
            regime = "uptrend"
        else:
            regime = "sideways"

        pr_out = {
            "tick": tick + 1,
            "mid": mid,
            "fast": fast,
            "slow": slow,
            "trend_raw": trend_raw,
            "trend_var": trend_var,
            "trend_z": trend_z,
            "regime": regime,
        }

        if PR_DEBUG and tick % PR_PRINT_EVERY == 0:
            print(
                "[PR REGIME] "
                f"tick={tick} mid={mid:.2f} fast={fast:.2f} slow={slow:.2f} "
                f"trend_z={trend_z:.2f} regime={regime}"
            )

        return pr_out, trend_z, regime

    def _galaxy_mm_sizes(self, sym: str, galaxy_score: float, signal_active: bool):
        """
        Returns buy_qty_limit, sell_qty_limit.
        score > 0 => buy-favoured.
        score < 0 => sell-favoured.
        """
        is_excluded = sym in EXCLUDE_PRODUCTS

        if is_excluded and GALAXY_REQUIRE_SIGNAL_FOR_EXCLUDED_TARGETS and not signal_active:
            return 0, 0

        abs_score = abs(galaxy_score)

        if is_excluded:
            if not signal_active:
                fav = unfav = GALAXY_EXCLUDED_NEUTRAL_QTY
            elif abs_score >= GALAXY_EXTREME_THRESHOLD:
                fav = GALAXY_EXCLUDED_EXTREME_FAV_QTY
                unfav = GALAXY_EXCLUDED_EXTREME_UNFAV_QTY
            elif abs_score >= GALAXY_STRONG_THRESHOLD:
                fav = GALAXY_EXCLUDED_STRONG_FAV_QTY
                unfav = GALAXY_EXCLUDED_STRONG_UNFAV_QTY
            else:
                fav = GALAXY_EXCLUDED_LIGHT_FAV_QTY
                unfav = GALAXY_EXCLUDED_LIGHT_UNFAV_QTY
        else:
            if not signal_active:
                fav = unfav = GALAXY_NEUTRAL_QTY
            elif abs_score >= GALAXY_EXTREME_THRESHOLD:
                fav = GALAXY_EXTREME_FAV_QTY
                unfav = GALAXY_EXTREME_UNFAV_QTY
            elif abs_score >= GALAXY_STRONG_THRESHOLD:
                fav = GALAXY_STRONG_FAV_QTY
                unfav = GALAXY_STRONG_UNFAV_QTY
            else:
                fav = GALAXY_LIGHT_FAV_QTY
                unfav = GALAXY_LIGHT_UNFAV_QTY

        if GALAXY_SUPPRESS_ADVERSE_SIDE and abs_score >= GALAXY_EXTREME_THRESHOLD:
            unfav = 0

        if galaxy_score > 0:
            return fav, unfav
        elif galaxy_score < 0:
            return unfav, fav
        else:
            return GALAXY_NEUTRAL_QTY, GALAXY_NEUTRAL_QTY

    def _apply_pr_regime_to_sizes(self, sym: str, buy_qty: int, sell_qty: int, pr_regime: str):
        """
        Product-specific risk gate for Planetary Rings.
        """
        if not PR_TREND_FILTER_ENABLE or sym != PR:
            return buy_qty, sell_qty

        if pr_regime == "downtrend":
            # Do not catch the falling knife.
            if PR_SUPPRESS_BUYS_IN_DOWNTREND:
                buy_qty = 0
            else:
                buy_qty = min(buy_qty, PR_TREND_UNFAV_QTY)

            # Still allow sell-side participation.
            sell_qty = max(sell_qty, PR_TREND_FAV_QTY)

        elif pr_regime == "uptrend":
            # Let PR participate long-side in uptrends.
            buy_qty = max(buy_qty, PR_TREND_FAV_QTY)

            if PR_SUPPRESS_SELLS_IN_UPTREND:
                sell_qty = 0
            else:
                sell_qty = min(sell_qty, PR_TREND_UNFAV_QTY)

        else:
            # Sideways: tiny two-sided MM only.
            buy_qty = min(buy_qty, PR_SIDEWAYS_QTY)
            sell_qty = min(sell_qty, PR_SIDEWAYS_QTY)

        return buy_qty, sell_qty

    def _apply_galaxy_price_improvement(
        self,
        sym: str,
        bb: int,
        ba: int,
        base_buy_px: int,
        base_sell_px: int,
        galaxy_score: float,
        pr_regime: str,
    ):
        """
        Moves only the favoured quote one extra tick closer to crossing.
        For PR, do not improve the wrong side in trend regimes.
        """
        if not GALAXY_PRICE_IMPROVE_ENABLE:
            return base_buy_px, base_sell_px

        spread = ba - bb
        if spread < GALAXY_PRICE_IMPROVE_MIN_SPREAD:
            return base_buy_px, base_sell_px

        is_excluded = sym in EXCLUDE_PRODUCTS

        threshold = GALAXY_PRICE_IMPROVE_THRESHOLD
        if is_excluded:
            threshold = GALAXY_EXCLUDED_PRICE_IMPROVE_THRESHOLD

        # PR-specific safety:
        # In PR downtrend, do not improve buy quote.
        # In PR uptrend, do not improve sell quote.
        if sym == PR and PR_TREND_FILTER_ENABLE:
            if pr_regime == "downtrend" and galaxy_score >= threshold:
                return base_buy_px, base_sell_px
            if pr_regime == "uptrend" and galaxy_score <= -threshold:
                return base_buy_px, base_sell_px

        if galaxy_score >= threshold:
            # Bullish: improve buy quote.
            improved_buy = min(ba - 1, base_buy_px + GALAXY_EXTRA_TICK)
            return improved_buy, base_sell_px

        if galaxy_score <= -threshold:
            # Bearish: improve sell quote.
            improved_sell = max(bb + 1, base_sell_px - GALAXY_EXTRA_TICK)
            return base_buy_px, improved_sell

        return base_buy_px, base_sell_px

    def _optimal_mm_orders(self, sym: str, od: OrderDepth, pos: int):
        """
        Position-ignorant max-size passive MM override.

        This deliberately ignores fair value and all strategy signals. The only
        state it respects is remaining position room under the ±10 limit.
        """
        if not od.buy_orders or not od.sell_orders:
            return []

        bb = max(od.buy_orders)
        ba = min(od.sell_orders)

        buy_px = int(bb + OPTIMAL_MM_EDGE)
        sell_px = int(ba - OPTIMAL_MM_EDGE)

        ords: List[Order] = []
        buy_room = POS_LIMIT - pos
        sell_room = POS_LIMIT + pos

        # Keep the quotes passive. With a 2-tick spread both sides can rest at
        # the same inside price, which matches the requested bb+1 / ba-1 logic.
        if buy_room > 0 and buy_px < ba:
            ords.append(Order(sym, buy_px, int(buy_room)))

        if sell_room > 0 and sell_px > bb:
            ords.append(Order(sym, sell_px, -int(sell_room)))

        return ords

    def _beast_mode_take_to_target(
        self,
        sym: str,
        od: OrderDepth,
        current_pos: int,
        target_pos: int,
    ):
        target_pos = max(-POS_LIMIT, min(POS_LIMIT, int(target_pos)))
        delta = target_pos - current_pos
        if delta == 0:
            return [], None, current_pos

        if delta > 0:
            ask = min(od.sell_orders)
            avail = abs(od.sell_orders[ask])
            qty = min(delta, avail, POS_LIMIT - current_pos)
            if qty > 0:
                return [Order(sym, int(ask), int(qty))], float(ask), current_pos + int(qty)
            return [], None, current_pos

        bid = max(od.buy_orders)
        avail = abs(od.buy_orders[bid])
        qty = min(-delta, avail, POS_LIMIT + current_pos)
        if qty > 0:
            return [Order(sym, int(bid), -int(qty))], float(bid), current_pos - int(qty)
        return [], None, current_pos

    def _beast_mode_orders(
        self,
        sym: str,
        state: TradingState,
        beast_in: dict,
        od: OrderDepth,
        bb: int,
        ba: int,
        mid: float,
    ):
        prev_mid = beast_in.get("p")
        same_count = int(beast_in.get("c", 0))
        armed = bool(beast_in.get("w", False))
        active = bool(beast_in.get("a", False))
        ready = bool(beast_in.get("r", True))
        exiting = bool(beast_in.get("x", False))
        loss_streak = int(beast_in.get("l", 0))
        level_change_count = int(beast_in.get("n", 0))
        cooldown_mid = beast_in.get("m")
        entry_side = beast_in.get("s")
        if entry_side == "L":
            entry_side = "long"
        elif entry_side == "S":
            entry_side = "short"
        entry_price = beast_in.get("e")
        last_closed_pnl = beast_in.get("q")

        if prev_mid is not None and float(mid) == float(prev_mid):
            same_count += 1
        else:
            same_count = 1

        mid_diff = None if prev_mid is None else float(mid) - float(prev_mid)

        if not ready:
            if cooldown_mid is None or float(mid) != float(cooldown_mid):
                ready = True
                cooldown_mid = None
                same_count = 1
                armed = False
            else:
                active = False

        current_pos = state.position.get(sym, 0)

        if ready and not active and not exiting and same_count >= BEAST_MODE_FLAT_STREAK_TO_ARM:
            armed = True
            loss_streak = 0
            level_change_count = 0
            last_closed_pnl = None

        handled_by_jump_mode = active or exiting
        orders: List[Order] = []

        if exiting:
            active = False
            armed = False
            if current_pos != 0:
                orders, _fill_price, expected_pos = self._beast_mode_take_to_target(sym, od, current_pos, 0)
                exiting = expected_pos != 0
            else:
                exiting = False

        if (active or armed) and mid_diff is not None and not exiting:
            abs_mid_diff = abs(float(mid_diff))
            if abs_mid_diff >= BEAST_MODE_TRUE_JUMP_EDGE:
                level_change_count = 0
            elif abs_mid_diff > BEAST_MODE_LEVEL_CHANGE_EDGE:
                level_change_count += 1

            if level_change_count >= BEAST_MODE_LEVEL_CHANGES_TO_EXIT:
                active = False
                armed = False
                ready = False
                cooldown_mid = float(mid)
                entry_side = None
                entry_price = None
                if current_pos != 0:
                    orders, _fill_price, expected_pos = self._beast_mode_take_to_target(sym, od, current_pos, 0)
                    exiting = expected_pos != 0
                level_change_count = 0

        if (active or armed) and mid_diff is not None and not exiting:
            desired_target = None
            entry_px_for_new_position = None

            if mid_diff > BEAST_MODE_JUMP_EDGE:
                desired_target = -POS_LIMIT
                entry_px_for_new_position = float(bb)
            elif mid_diff < -BEAST_MODE_JUMP_EDGE:
                desired_target = POS_LIMIT
                entry_px_for_new_position = float(ba)

            if desired_target is not None:
                active = True
                armed = False
                handled_by_jump_mode = True
                target = desired_target

                pending_closing_pnl = None
                pending_loss_streak = loss_streak
                force_mode_exit = False
                if current_pos > 0 and target <= 0 and entry_side == "long" and entry_price is not None:
                    pending_closing_pnl = float(bb) - float(entry_price)
                elif current_pos < 0 and target >= 0 and entry_side == "short" and entry_price is not None:
                    pending_closing_pnl = float(entry_price) - float(ba)

                if pending_closing_pnl is not None:
                    if pending_closing_pnl < 0:
                        pending_loss_streak += 1
                    else:
                        pending_loss_streak = 0

                    if pending_loss_streak >= BEAST_MODE_MAX_LOSS_STREAK:
                        # Third consecutive losing close: flatten only and
                        # require a fresh unchanged-mid streak before re-entry.
                        target = 0
                        force_mode_exit = True

                orders, fill_price, expected_pos = self._beast_mode_take_to_target(sym, od, current_pos, target)

                if orders:
                    if pending_closing_pnl is not None:
                        closed_long = current_pos > 0 and expected_pos <= 0
                        closed_short = current_pos < 0 and expected_pos >= 0
                        if closed_long or closed_short:
                            last_closed_pnl = pending_closing_pnl
                            loss_streak = pending_loss_streak

                            if force_mode_exit:
                                active = False
                                ready = False
                                armed = False
                                cooldown_mid = float(mid)
                                exiting = expected_pos != 0
                                level_change_count = 0

                    if expected_pos > 0:
                        if current_pos <= 0 or entry_side != "long":
                            entry_side = "long"
                            entry_price = float(fill_price if fill_price is not None else entry_px_for_new_position)
                    elif expected_pos < 0:
                        if current_pos >= 0 or entry_side != "short":
                            entry_side = "short"
                            entry_price = float(fill_price if fill_price is not None else entry_px_for_new_position)
                    else:
                        entry_side = None
                        entry_price = None

        if not active and not ready:
            entry_side = None
            entry_price = None

        beast_out = {
            "p": float(mid),
            "c": same_count,
        }
        if armed:
            beast_out["w"] = 1
        if active:
            beast_out["a"] = 1
        if not ready:
            beast_out["r"] = 0
            if cooldown_mid is not None:
                beast_out["m"] = float(cooldown_mid)
        if exiting:
            beast_out["x"] = 1
        if loss_streak:
            beast_out["l"] = loss_streak
        if level_change_count:
            beast_out["n"] = level_change_count
        if entry_side == "long":
            beast_out["s"] = "L"
        elif entry_side == "short":
            beast_out["s"] = "S"
        if entry_price is not None:
            beast_out["e"] = float(entry_price)
        if mid_diff is not None:
            beast_out["d"] = float(mid_diff)
        if last_closed_pnl is not None:
            beast_out["q"] = float(last_closed_pnl)

        return beast_out, orders, handled_by_jump_mode

    def _best_bid_ask_mid(self, state: TradingState, sym: str):
        od = state.order_depths.get(sym)
        if od is None or not od.buy_orders or not od.sell_orders:
            return None, None, None, None
        bb = max(od.buy_orders)
        ba = min(od.sell_orders)
        return od, bb, ba, (bb + ba) / 2.0

    def _take_vanilla_to_target(self, od: OrderDepth, current_pos: int, target_pos: int):
        target_pos = max(-POS_LIMIT, min(POS_LIMIT, int(target_pos)))
        delta = target_pos - current_pos
        if delta == 0:
            return []

        if delta > 0:
            ask = min(od.sell_orders)
            avail = abs(od.sell_orders[ask])
            qty = min(delta, avail, POS_LIMIT - current_pos)
            if qty > 0:
                return [Order(PAIR_B, int(ask), int(qty))]
            return []

        bid = max(od.buy_orders)
        avail = abs(od.buy_orders[bid])
        qty = min(-delta, avail, POS_LIMIT + current_pos)
        if qty > 0:
            return [Order(PAIR_B, int(bid), -int(qty))]
        return []

    def _take_product_to_target(self, sym: str, od: OrderDepth, current_pos: int, target_pos: int):
        target_pos = max(-POS_LIMIT, min(POS_LIMIT, int(target_pos)))
        delta = target_pos - current_pos
        if delta == 0:
            return []

        if delta > 0:
            ask = min(od.sell_orders)
            avail = abs(od.sell_orders[ask])
            qty = min(delta, avail, POS_LIMIT - current_pos)
            if qty > 0:
                return [Order(sym, int(ask), int(qty))]
            return []

        bid = max(od.buy_orders)
        avail = abs(od.buy_orders[bid])
        qty = min(-delta, avail, POS_LIMIT + current_pos)
        if qty > 0:
            return [Order(sym, int(bid), -int(qty))]
        return []

    def _single_product_mr_orders(self, sym: str, od: OrderDepth, mid: float, current_pos: int, mr_state_in: dict):
        cfg = SINGLE_MR_PRODUCTS[sym]
        alpha_ema = 2.0 / (float(cfg["ema"]) + 1.0)

        prev_ema = mr_state_in.get("ema", mid)
        ema = (1.0 - alpha_ema) * prev_ema + alpha_ema * mid
        residual = mid - ema

        residuals = self._single_mr_residuals.setdefault(sym, [])
        residual_sum = self._single_mr_residual_sum.get(sym, 0.0)
        residual_sumsq = self._single_mr_residual_sumsq.get(sym, 0.0)

        residuals.append(residual)
        residual_sum += residual
        residual_sumsq += residual * residual

        z_window = int(cfg["z_window"])
        if len(residuals) > z_window:
            old = residuals.pop(0)
            residual_sum -= old
            residual_sumsq -= old * old

        self._single_mr_residual_sum[sym] = residual_sum
        self._single_mr_residual_sumsq[sym] = residual_sumsq

        count = len(residuals)
        min_obs = int(cfg.get("min_obs", max(5, z_window // 5)))
        if count >= min_obs and count >= 2:
            variance = (residual_sumsq - (residual_sum * residual_sum) / count) / (count - 1)
            std = max(1.0, math.sqrt(max(0.0, variance)))
            z = residual / std
        else:
            std = 0.0
            z = None

        max_pos = min(POS_LIMIT, int(cfg.get("max_pos", POS_LIMIT)))
        target = int(mr_state_in.get("desired", current_pos))
        target = max(-max_pos, min(max_pos, target))

        if current_pos == 0 and target != 0:
            target = 0

        if z is not None:
            if current_pos == 0:
                if z <= float(cfg["buy_z"]):
                    target = max_pos
                elif z >= float(cfg["sell_z"]):
                    target = -max_pos
            elif current_pos > 0:
                if z >= float(cfg["exit_long_at_z"]):
                    target = 0
                elif z <= float(cfg["buy_z"]):
                    target = max_pos
            elif current_pos < 0:
                if z <= float(cfg["exit_short_at_z"]):
                    target = 0
                elif z >= float(cfg["sell_z"]):
                    target = -max_pos

        orders = self._take_product_to_target(sym, od, current_pos, target)
        mr_state_out = {
            "ema": ema,
            "std": std,
            "residual": residual,
            "z": z,
            "count": count,
            "desired": target,
        }
        return mr_state_out, orders

    def _vanilla_spread_orders(self, state: TradingState, spread_state_in: dict):
        od_choco, _bb_choco, _ba_choco, mid_choco = self._best_bid_ask_mid(state, PAIR_A)
        od_vanilla, _bb_vanilla, _ba_vanilla, mid_vanilla = self._best_bid_ask_mid(state, PAIR_B)
        if od_choco is None or od_vanilla is None:
            return dict(spread_state_in), []

        spread = mid_choco - VANILLA_SPREAD_HEDGE * mid_vanilla
        alpha_ema = 2.0 / (VANILLA_SPREAD_EMA_W + 1.0)

        prev_ema = spread_state_in.get("ema", spread)
        ema = (1.0 - alpha_ema) * prev_ema + alpha_ema * spread
        residual = spread - ema

        self._vanilla_residuals.append(residual)
        self._vanilla_residual_sum += residual
        self._vanilla_residual_sumsq += residual * residual
        if len(self._vanilla_residuals) > VANILLA_SPREAD_Z_W:
            old = self._vanilla_residuals.pop(0)
            self._vanilla_residual_sum -= old
            self._vanilla_residual_sumsq -= old * old

        count = len(self._vanilla_residuals)
        if count >= VANILLA_SPREAD_MIN_Z_OBS and count >= 2:
            variance = (self._vanilla_residual_sumsq - (self._vanilla_residual_sum * self._vanilla_residual_sum) / count) / (count - 1)
            std = max(1.0, math.sqrt(max(0.0, variance)))
            z = residual / std
        else:
            std = 0.0
            z = None

        pos = state.position.get(PAIR_B, 0)
        target = int(spread_state_in.get("desired_vanilla", pos))
        target = max(-VANILLA_SPREAD_MAX_POS, min(VANILLA_SPREAD_MAX_POS, target))

        if pos == 0 and target != 0:
            target = 0

        if z is not None:
            if pos == 0:
                if z <= VANILLA_SPREAD_BUY_Z:
                    # Spread low means Chocolate cheap / Vanilla rich. Short Vanilla.
                    target = -VANILLA_SPREAD_MAX_POS
                elif z >= VANILLA_SPREAD_SELL_Z:
                    # Spread high means Chocolate rich / Vanilla cheap. Long Vanilla.
                    target = VANILLA_SPREAD_MAX_POS
            elif pos > 0:
                if z <= -VANILLA_SPREAD_EXIT_Z:
                    target = 0
                elif z >= VANILLA_SPREAD_SELL_Z:
                    target = VANILLA_SPREAD_MAX_POS
            elif pos < 0:
                if z >= VANILLA_SPREAD_EXIT_Z:
                    target = 0
                elif z <= VANILLA_SPREAD_BUY_Z:
                    target = -VANILLA_SPREAD_MAX_POS

        orders = self._take_vanilla_to_target(od_vanilla, pos, target)

        if VANILLA_SPREAD_ADD_MM and not orders:
            orders = self._optimal_mm_orders(PAIR_B, od_vanilla, pos)

        spread_state_out = {
            "ema": ema,
            "std": std,
            "spread": spread,
            "residual": residual,
            "z": z,
            "count": count,
            "desired_vanilla": target,
        }
        return spread_state_out, orders

    def run(self, state: TradingState):
        try:
            td_in = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td_in = {}

        ema_state = td_in.get("ema", {})
        var_state = td_in.get("var", {})

        new_ema = {}
        new_var = {}

        eta = 2.0 / (TAKE_EMA_W + 1.0)

        result: Dict[str, List[Order]] = {}

        galaxy_targets = self._galaxy_targets()
        galaxy_state_out, galaxy_score, galaxy_signal_active = self._update_galaxy_signal(state, td_in)
        planetary_state_out, pr_trend_z, pr_regime = self._update_planetary_regime(state, td_in)
        vanilla_spread_in = td_in.get("vanilla_spread", {})
        vanilla_spread_out = dict(vanilla_spread_in)
        single_mr_in = td_in.get("single_mr", {})
        single_mr_out = {}
        beast_mode_in = td_in.get("beast_mode", {})
        beast_mode_out = {}

        for sym in ALL_PRODUCTS:
            is_optimal_mm = sym in OPTIMAL_MM_PRODUCTS
            is_galaxy_target = GALAXY_OVERLAY_ENABLE and sym in galaxy_targets and not is_optimal_mm

            od = state.order_depths.get(sym)
            if od is None or not od.buy_orders or not od.sell_orders:
                new_ema[sym] = ema_state.get(sym, 0.0)
                new_var[sym] = var_state.get(sym, 1.0)
                continue

            bb = max(od.buy_orders)
            ba = min(od.sell_orders)
            mid = (bb + ba) / 2.0

            if BEAST_MODE_ENABLE:
                sym_beast_out, beast_orders, beast_active = self._beast_mode_orders(
                    sym,
                    state,
                    beast_mode_in.get(sym, {}),
                    od,
                    bb,
                    ba,
                    mid,
                )
                beast_mode_out[sym] = sym_beast_out
                if beast_active:
                    if beast_orders:
                        result[sym] = beast_orders
                    new_ema[sym] = ema_state.get(sym, mid)
                    new_var[sym] = var_state.get(sym, 1.0)
                    continue

            # v18b: Trend follow priority over EXCLUDE
            if sym in TREND_FOLLOW_PRODUCTS:
                prev_ema_tf = ema_state.get(sym, mid)
                ema_tf = (1.0 - eta) * prev_ema_tf + eta * mid
                new_ema[sym] = ema_tf
                dev_tf = mid - ema_tf
                prev_var_tf = var_state.get(sym, 1.0)
                var_tf = (1.0 - eta) * prev_var_tf + eta * (dev_tf * dev_tf)
                new_var[sym] = var_tf
                std_tf = max(1.0, math.sqrt(var_tf))
                edge_tf = max(TREND_FOLLOW_MIN_EDGE, TREND_FOLLOW_K * std_tf)
                pos_tf = state.position.get(sym, 0)
                ords_tf: List[Order] = []
                if mid > ema_tf + edge_tf and pos_tf < POS_LIMIT:
                    avail = abs(od.sell_orders[ba])
                    q = min(avail, POS_LIMIT - pos_tf)
                    if q > 0:
                        ords_tf.append(Order(sym, int(ba), int(q)))
                if mid < ema_tf - edge_tf and pos_tf > -POS_LIMIT:
                    avail = abs(od.buy_orders[bb])
                    q = min(avail, POS_LIMIT + pos_tf)
                    if q > 0:
                        ords_tf.append(Order(sym, int(bb), -int(q)))
                if ords_tf:
                    result[sym] = ords_tf
                continue

            if SINGLE_MR_ENABLE and sym in SINGLE_MR_PRODUCTS:
                single_mr_state, single_mr_orders = self._single_product_mr_orders(
                    sym,
                    od,
                    mid,
                    state.position.get(sym, 0),
                    single_mr_in.get(sym, {}),
                )
                single_mr_out[sym] = single_mr_state
                if single_mr_orders:
                    result[sym] = single_mr_orders
                new_ema[sym] = ema_state.get(sym, mid)
                new_var[sym] = var_state.get(sym, 1.0)
                continue

            if VANILLA_SPREAD_ENABLE and sym == PAIR_B:
                vanilla_spread_out, vanilla_orders = self._vanilla_spread_orders(state, vanilla_spread_in)
                if vanilla_orders:
                    result[PAIR_B] = vanilla_orders
                new_ema[sym] = ema_state.get(sym, mid)
                new_var[sym] = var_state.get(sym, 1.0)
                continue

            # Original skip logic, but allow selected Galaxy overlay and optimal
            # MM override targets through. Beast mode has first priority, even
            # for otherwise excluded products, once an armed jump actually fires.
            if sym in EXCLUDE_PRODUCTS and not is_galaxy_target and not is_optimal_mm:
                new_ema[sym] = ema_state.get(sym, 0.0)
                new_var[sym] = var_state.get(sym, 1.0)
                continue

            # Optimal MM override: no paje takes, no existing MM, no overlays.
            if is_optimal_mm:
                pos = state.position.get(sym, 0)
                ords = self._optimal_mm_orders(sym, od, pos)
                if ords:
                    result[sym] = ords
                new_ema[sym] = ema_state.get(sym, mid)
                new_var[sym] = var_state.get(sym, 1.0)
                continue

            # Update EMA + variance for every active product.
            prev_ema = ema_state.get(sym, mid)
            ema = (1.0 - eta) * prev_ema + eta * mid
            new_ema[sym] = ema

            dev = mid - ema
            prev_var = var_state.get(sym, 1.0)
            var = (1.0 - eta) * prev_var + eta * (dev * dev)
            new_var[sym] = var

            std = max(1.0, math.sqrt(var))
            k_eff = TAKE_K_OVERRIDE.get(sym, TAKE_K)
            take_edge = max(TAKE_MIN_EDGE, k_eff * std)

            pos = state.position.get(sym, 0)

            ords: List[Order] = []

            took_buy = 0
            took_sell = 0

            # For excluded Galaxy overlay targets, do not use base EMA takes by default.
            allow_base_take = TAKE_ENABLE
            if is_galaxy_target and sym in EXCLUDE_PRODUCTS and not GALAXY_ALLOW_BASE_TAKE_ON_EXCLUDED_TARGETS:
                allow_base_take = False

            # Layer 1: original opportunistic EMA take.
            if allow_base_take:
                # cheap -> buy
                if ba < ema - take_edge and pos < POS_LIMIT:
                    avail = abs(od.sell_orders[ba])
                    q = min(avail, POS_LIMIT - pos)
                    if q > 0:
                        ords.append(Order(sym, int(ba), int(q)))
                        took_buy = q
                        pos += q

                # rich -> sell
                if bb > ema + take_edge and pos > -POS_LIMIT:
                    avail = abs(od.buy_orders[bb])
                    q = min(avail, POS_LIMIT + pos)
                    if q > 0:
                        ords.append(Order(sym, int(bb), -int(q)))
                        took_sell = q
                        pos -= q

            # Layer 1B: PR emergency flattening.
            # If PR is downtrending and we are long, flatten at bid.
            if (
                PR_TREND_FILTER_ENABLE
                and sym == PR
                and pr_regime == "downtrend"
                and PR_FLATTEN_LONGS_IN_DOWNTREND
                and pos > 0
            ):
                avail = abs(od.buy_orders[bb])
                q = min(avail, pos, PR_FLATTEN_LONG_TAKE_QTY)

                if q > 0:
                    ords.append(Order(sym, int(bb), -int(q)))
                    took_sell += q
                    pos -= q

            # Layer 2: standing MM, with Galaxy overlay if active.
            if ba - bb >= 2:
                buy_room = POS_LIMIT - pos - took_buy
                sell_room = POS_LIMIT + pos - took_sell

                buy_px = bb + MM_EDGE
                sell_px = ba - MM_EDGE

                # Original inventory skew is effectively inactive because MM_SKEW_THR=100.
                if pos > MM_SKEW_THR:
                    buy_px -= 1
                    sell_px -= 1
                elif pos < -MM_SKEW_THR:
                    buy_px += 1
                    sell_px += 1

                buy_px = max(buy_px, 1)

                if is_galaxy_target:
                    buy_qty_limit, sell_qty_limit = self._galaxy_mm_sizes(
                        sym,
                        galaxy_score,
                        galaxy_signal_active,
                    )

                    buy_qty_limit, sell_qty_limit = self._apply_pr_regime_to_sizes(
                        sym,
                        buy_qty_limit,
                        sell_qty_limit,
                        pr_regime,
                    )

                    buy_px, sell_px = self._apply_galaxy_price_improvement(
                        sym,
                        bb,
                        ba,
                        int(buy_px),
                        int(sell_px),
                        galaxy_score,
                        pr_regime,
                    )
                else:
                    buy_qty_limit = MM_QTY
                    sell_qty_limit = MM_QTY

                if buy_room > 0 and buy_qty_limit > 0 and buy_px < ba:
                    q = min(buy_qty_limit, buy_room)
                    if q > 0:
                        ords.append(Order(sym, int(buy_px), int(q)))

                if sell_room > 0 and sell_qty_limit > 0 and sell_px > bb:
                    q = min(sell_qty_limit, sell_room)
                    if q > 0:
                        ords.append(Order(sym, int(sell_px), -int(q)))

            if ords:
                result[sym] = ords

        td_out = {
            "ema": new_ema,
            "var": new_var,
            "galaxy": galaxy_state_out,
            "planetary_regime": planetary_state_out,
            "vanilla_spread": vanilla_spread_out,
            "single_mr": single_mr_out,
            "beast_mode": beast_mode_out,
        }

        try:
            trader_data = json.dumps(td_out, separators=(",", ":"))
        except Exception:
            trader_data = ""

        return result, 0, trader_data