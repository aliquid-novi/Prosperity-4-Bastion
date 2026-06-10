"""
Round 1 v13 — Combined best of our v11 + Ante's PEPPER improvements
=====================================================================

OSMIUM (unchanged from v11):
  Z-score(fixed 10000) mean-reversion + rolling_std normalization
  MM_EDGE_EMPTY = -89 (better than Ante's -85, confirmed by log comparison)
  Website (v11): 4,657

PEPPER (Ante's linear-fair approach):
  Fair = round(start_mid/1000)*1000 + 0.1 × (timestamp/100)
       = fair_base + timestamp × 0.001
  CORE=70 (sweep asks immediately)
  SOFT_TOP=75 (trim when exceeded)
  Buy at ask ≤ fair + 0 (tight threshold, opportunistic)
  Passive ask floor at fair + 3 (prevents selling below fair)
  Ante website: 7,783 (+295 vs our v11)

Expected website: 4,657 + 7,783 = 12,440 (+295 vs v11's 12,145)

Safety verified:
  bid()/ask() methods enforce max_buy/max_sell limits
  Manual max_buy decrement in merge branch of opportunistic step
"""
import json
from typing import Optional
from datamodel import OrderDepth, TradingState, Order


# ── ASH_COATED_OSMIUM (our v11, unchanged) ─────────────────────────
ASH                  = "ASH_COATED_OSMIUM"
ASH_POS_LIMIT        = 80
ASH_HIST_MAX         = 60
ASH_HIST_MIN_MID     = 9900
ASH_FAIR_WINDOW      = 20
ASH_ZSCORE_SCALE     = 15
ASH_TARGET_MAX       = 65
ASH_MM_EDGE          = 1
ASH_MM_EDGE_EMPTY    = -89   # ← our value (Ante uses -85, ours is better)


# ── INTARIAN_PEPPER_ROOT (Ante's new linear-fair approach) ─────────
IPR                        = "INTARIAN_PEPPER_ROOT"
IPR_POS_LIMIT              = 80
IPR_CORE                   = 70    # sweep asks immediately to reach this
IPR_SOFT_TOP               = 75    # trim (sell) when position exceeds this
IPR_FAIR_RATE              = 0.1   # fair price increase per 100-unit timestamp step
IPR_POSITION_RETURN_EDGE   = 0.0   # buy at ask ≤ fair + this (grid-searched optimal)
IPR_MM_EDGE_EMPTY          = -105  # safety net when one book side is missing


# ── Logger ──────────────────────────────────────────────────────────
class Logger:
    def __init__(self):
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects, sep=" ", end="\n"):
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state, orders, conversions, trader_data):
        base_length = len(self.to_json([
            self.compress_state(state, ""),
            self.compress_orders(orders),
            conversions, "", "",
        ]))
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders), conversions,
            self.truncate(trader_data, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state, td):
        return [
            state.timestamp, td,
            [[l.symbol, l.product, l.denomination] for l in state.listings.values()],
            {s: [od.buy_orders, od.sell_orders] for s, od in state.order_depths.items()},
            [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
             for arr in state.own_trades.values() for t in arr],
            [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
             for arr in state.market_trades.values() for t in arr],
            state.position,
            [state.observations.plainValueObservations, {}],
        ]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity]
                for arr in orders.values() for o in arr]

    def to_json(self, v):
        return json.dumps(v, separators=(",", ":"))

    def truncate(self, v, ml):
        return v if len(v) <= ml else v[:ml - 3] + "..."


logger = Logger()


# ── Base class with max_buy/max_sell enforcement ────────────────────
class ProductTrader:
    def __init__(self, name: str, state: TradingState, pos_limit: int):
        self.name     = name
        self.position = state.position.get(name, 0)
        self.orders: list = []

        od = state.order_depths.get(name, OrderDepth())
        self.buy_orders  = {p: abs(v) for p, v in sorted(od.buy_orders.items(),  reverse=True)}
        self.sell_orders = {p: abs(v) for p, v in sorted(od.sell_orders.items())}

        self.best_bid  = max(self.buy_orders)  if self.buy_orders  else None
        self.best_ask  = min(self.sell_orders) if self.sell_orders else None
        self.worst_bid = min(self.buy_orders)  if self.buy_orders  else None
        self.worst_ask = max(self.sell_orders) if self.sell_orders else None

        self.wide_mid = ((self.worst_bid + self.worst_ask) / 2
                         if (self.worst_bid and self.worst_ask) else None)
        self.mid      = ((self.best_bid + self.best_ask) / 2
                         if (self.best_bid and self.best_ask) else None)

        self.max_buy  = pos_limit - self.position
        self.max_sell = pos_limit + self.position

    def bid(self, price, volume):
        vol = min(abs(int(volume)), self.max_buy)
        if vol > 0:
            self.orders.append(Order(self.name, int(price), vol))
            self.max_buy -= vol

    def ask(self, price, volume):
        vol = min(abs(int(volume)), self.max_sell)
        if vol > 0:
            self.orders.append(Order(self.name, int(price), -vol))
            self.max_sell -= vol


# ── OSMIUM: our v11 z-score strategy (unchanged) ────────────────────
class OsmiumTrader(ProductTrader):
    def __init__(self, state, hist, last_normal_bid, last_normal_ask):
        super().__init__(ASH, state, ASH_POS_LIMIT)
        self.hist = list(hist)
        if self.mid is not None and self.mid > ASH_HIST_MIN_MID:
            self.hist.append(self.mid)
        self.hist = self.hist[-ASH_HIST_MAX:]
        self.last_normal_bid = last_normal_bid
        self.last_normal_ask = last_normal_ask
        self.out_last_normal_bid = last_normal_bid
        self.out_last_normal_ask = last_normal_ask

    def _fair_and_target(self):
        if len(self.hist) < ASH_FAIR_WINDOW:
            return None, 0
        window = self.hist[-ASH_FAIR_WINDOW:]
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / len(window)
        std = var ** 0.5
        if std < 0.01:
            return mean, 0
        # Z-score deviation from FIXED 10000 (Ante's insight)
        z = (self.hist[-1] - 10000) / std
        target = int(-z * ASH_ZSCORE_SCALE)
        target = max(-ASH_TARGET_MAX, min(ASH_TARGET_MAX, target))
        return mean, target

    def get_orders(self):
        have_bid = self.best_bid is not None
        have_ask = self.best_ask is not None
        if have_bid and have_ask:
            self.out_last_normal_bid = self.best_bid
            self.out_last_normal_ask = self.best_ask

        fair, target = self._fair_and_target()

        # Taking toward target position
        if fair is not None:
            if self.position < target and have_ask and self.best_ask <= fair:
                qty = min(target - self.position,
                          self.sell_orders.get(self.best_ask, 0), self.max_buy)
                if qty > 0:
                    self.bid(self.best_ask, qty)
            elif self.position > target and have_bid and self.best_bid >= fair:
                qty = min(self.position - target,
                          self.buy_orders.get(self.best_bid, 0), self.max_sell)
                if qty > 0:
                    self.ask(self.best_bid, qty)

        # Making + safety net for empty-book events
        if have_bid and have_ask:
            self.bid(self.best_bid + ASH_MM_EDGE, self.max_buy)
            self.ask(self.best_ask - ASH_MM_EDGE, self.max_sell)
        elif have_bid:
            self.bid(self.best_bid + ASH_MM_EDGE, self.max_buy)
            if self.last_normal_ask is not None:
                self.ask(self.last_normal_ask - ASH_MM_EDGE_EMPTY, self.max_sell)
        elif have_ask:
            if self.last_normal_bid is not None:
                self.bid(self.last_normal_bid + ASH_MM_EDGE_EMPTY, self.max_buy)
            self.ask(self.best_ask - ASH_MM_EDGE, self.max_sell)
        else:
            if self.last_normal_bid is not None:
                self.bid(self.last_normal_bid + ASH_MM_EDGE_EMPTY, self.max_buy)
            if self.last_normal_ask is not None:
                self.ask(self.last_normal_ask - ASH_MM_EDGE_EMPTY, self.max_sell)

        return self.orders

    def get_td(self):
        d: dict = {"h": self.hist[-40:]}
        if self.out_last_normal_bid is not None: d["nb"] = self.out_last_normal_bid
        if self.out_last_normal_ask is not None: d["na"] = self.out_last_normal_ask
        return d


# ── PEPPER: Ante's linear-fair strategy ─────────────────────────────
class PepperTrader(ProductTrader):
    def __init__(self, state, last_normal_bid, last_normal_ask, fair_base, prev_ts):
        super().__init__(IPR, state, IPR_POS_LIMIT)
        self.last_normal_bid = last_normal_bid
        self.last_normal_ask = last_normal_ask
        self.out_last_normal_bid = last_normal_bid
        self.out_last_normal_ask = last_normal_ask

        # Detect fair base at first tick or when a new day starts (timestamp resets).
        # Robust fallback chain for when first tick has partial/empty book —
        # avoids hardcoded 12000 leaking into an unseen day that starts elsewhere.
        new_day = (fair_base is None) or (prev_ts is not None and state.timestamp < prev_ts)
        if new_day:
            if self.mid is not None:
                # Both sides present — most common case
                self.fair_base = round(self.mid / 1000) * 1000
            elif self.wide_mid is not None:
                # Both walls present — use wall mid
                self.fair_base = round(self.wide_mid / 1000) * 1000
            elif self.best_bid is not None:
                # Only bid side — estimate mid as best_bid + half_spread(~6)
                self.fair_base = round((self.best_bid + 6) / 1000) * 1000
            elif self.best_ask is not None:
                # Only ask side — estimate mid as best_ask - half_spread
                self.fair_base = round((self.best_ask - 6) / 1000) * 1000
            else:
                # No market data at all — defer; _fair() will return None and
                # get_orders() will skip normal MM until initialized
                self.fair_base = None
        else:
            self.fair_base = fair_base
        self._ts = state.timestamp

    def _fair(self):
        # Linear fair: round start_mid + 0.1 per 100-ts step.
        # Returns None if fair_base couldn't be established yet (rare: empty book on tick 0).
        if self.fair_base is None:
            return None
        return self.fair_base + (self._ts / 100) * IPR_FAIR_RATE

    def get_orders(self):
        have_bid = self.best_bid is not None
        have_ask = self.best_ask is not None
        if have_bid and have_ask:
            self.out_last_normal_bid = self.best_bid
            self.out_last_normal_ask = self.best_ask

        fair = self._fair()

        # Skip normal MM if fair couldn't be established yet (tick 0 with empty book).
        # Safety-net block at end will still run for dummy-taker opportunities.
        if fair is None:
            if not have_bid and self.last_normal_bid is not None and self.max_buy > 0:
                self.bid(self.last_normal_bid + IPR_MM_EDGE_EMPTY, self.max_buy)
            if not have_ask and self.last_normal_ask is not None and self.max_sell > 0:
                self.ask(self.last_normal_ask - IPR_MM_EDGE_EMPTY, self.max_sell)
            return self.orders

        if have_bid and have_ask:
            # Step 1: Sweep asks to reach CORE (no price filter — drift > cost)
            if self.position < IPR_CORE and self.max_buy > 0:
                need = IPR_CORE - self.position
                for price in sorted(self.sell_orders.keys()):
                    if need <= 0 or self.max_buy <= 0: break
                    qty = min(self.sell_orders[price], need, self.max_buy)
                    if qty > 0:
                        self.bid(price, qty)
                        need -= qty

            # Step 2: Opportunistic buy at ask ≤ fair + edge (strict, linear fair)
            if self.max_buy > 0:
                for price in sorted(self.sell_orders.keys()):
                    if self.max_buy <= 0: break
                    if price <= fair + IPR_POSITION_RETURN_EDGE:
                        existing = next(
                            (o for o in self.orders
                             if o.symbol == IPR and o.price == int(price) and o.quantity > 0),
                            None,
                        )
                        already = existing.quantity if existing else 0
                        avail = self.sell_orders[price] - already
                        if avail > 0:
                            extra = min(avail, self.max_buy)
                            if extra > 0:
                                if existing:
                                    # Merge into step-1 order (manual max_buy decrement)
                                    existing.quantity += extra
                                    self.max_buy -= extra
                                else:
                                    self.bid(price, extra)

            # Step 3: Trim if position > SOFT_TOP
            if self.position > IPR_SOFT_TOP and self.max_sell > 0:
                excess = self.position - IPR_SOFT_TOP
                for price in sorted(self.buy_orders.keys(), reverse=True):
                    if excess <= 0 or self.max_sell <= 0: break
                    if price >= self.wide_mid + 1:
                        qty = min(self.buy_orders[price], excess, self.max_sell)
                        if qty > 0:
                            self.ask(price, qty)
                            excess -= qty

            # Step 4: Book-aware passive bid/ask prices (overbid/undercut scan)
            bp = int(self.worst_bid + 1)
            ap = int(self.worst_ask - 1)
            for price, vol in self.buy_orders.items():
                one_above = price + 1
                if vol > 1 and one_above < self.wide_mid:
                    bp = max(bp, one_above); break
                elif price < self.wide_mid:
                    bp = max(bp, price); break
            for price, vol in self.sell_orders.items():
                one_below = price - 1
                if vol > 1 and one_below > self.wide_mid:
                    ap = min(ap, one_below); break
                elif price > self.wide_mid:
                    ap = min(ap, price); break

            if self.best_ask is not None: bp = min(bp, self.best_ask - 1)
            if self.best_bid is not None: ap = max(ap, self.best_bid + 1)

            # Floor passive ask at fair + 3 — key Ante improvement, prevents selling below fair
            if ap < fair:
                ap = int(fair) + 3

            # Step 5: Post passive bid and ask with LIMITED capacity.
            # Ante fix: passive ask should never drive us below CORE, otherwise
            # a bot hitting our ask at pos=75 could take many units (pos→65),
            # forcing step-1 to buy back → wasted spread. Apply only when the
            # book has both sides; one-sided branches intentionally keep full
            # max_sell to allow dummy-taker safety net.
            if self.max_buy > 0:
                self.bid(bp, self.max_buy)
            active_already_sold = (IPR_POS_LIMIT + self.position) - self.max_sell
            passive_ask_qty = max(0, (self.position - IPR_CORE) - active_already_sold)
            passive_ask_qty = min(passive_ask_qty, self.max_sell)
            if passive_ask_qty > 0 and ap != bp:
                self.ask(ap, passive_ask_qty)

        # One-sided book handling
        elif have_bid and self.max_buy > 0:
            self.bid(self.best_bid + 1, self.max_buy)
        elif have_ask and self.max_sell > 0:
            ap1 = self.best_ask - 1
            if ap1 < fair:
                ap1 = int(fair) + 3
            self.ask(ap1, self.max_sell)

        # Safety net: deep orders when one side is missing
        if not have_bid and self.last_normal_bid is not None and self.max_buy > 0:
            self.bid(self.last_normal_bid + IPR_MM_EDGE_EMPTY, self.max_buy)
        if not have_ask and self.last_normal_ask is not None and self.max_sell > 0:
            self.ask(self.last_normal_ask - IPR_MM_EDGE_EMPTY, self.max_sell)

        return self.orders


# ── Trader entry point ──────────────────────────────────────────────
class Trader:
    # MAF bid: return 0 — we don't compete for extra 25% volume (Phase 2 is reset anyway,
    # so optimizing R2 further is low ROI). Participating with bid=0 keeps us in median
    # calc as a 0-bidder; almost certainly won't win auction, pays 0 if we somehow do.
    def bid(self): return 0

    def run(self, state: TradingState):
        result = {}
        conversions = 0
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}
        new_td = {}

        if ASH in state.order_depths:
            try:
                ash_prev = td.get("o", {})
                ash = OsmiumTrader(
                    state,
                    hist            = ash_prev.get("h", []),
                    last_normal_bid = ash_prev.get("nb"),
                    last_normal_ask = ash_prev.get("na"),
                )
                result[ASH] = ash.get_orders()
                new_td["o"] = ash.get_td()
            except Exception:
                result[ASH] = []

        if IPR in state.order_depths:
            try:
                ipr = PepperTrader(
                    state,
                    last_normal_bid = td.get("ipr_nb"),
                    last_normal_ask = td.get("ipr_na"),
                    fair_base       = td.get("ipr_fb"),
                    prev_ts         = td.get("ipr_pt"),
                )
                result[IPR] = ipr.get_orders()
                if ipr.out_last_normal_bid is not None: new_td["ipr_nb"] = ipr.out_last_normal_bid
                if ipr.out_last_normal_ask is not None: new_td["ipr_na"] = ipr.out_last_normal_ask
                # Only persist fair_base once it's been successfully established
                if ipr.fair_base is not None: new_td["ipr_fb"] = ipr.fair_base
                new_td["ipr_pt"] = state.timestamp
            except Exception:
                result[IPR] = []

        trader_data = json.dumps(new_td, separators=(",", ":"))
        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data