IMC Prosperity 4, 2026 edition, was a global algorithmic trading competition with thousands of teams from around the world. Over five rounds spread across roughly two weeks, teams trade a fictional set of products on a fictional island, with the goal of growing as much virtual capital ("SeaShells") as possible.

Each round has two parts:

- **Algorithmic trading** — you submit a Python `Trader` class that runs tick-by-tick against a simulated exchange. On every timestamp you receive the current `TradingState` (order books, your position, recent trades, observations) and return the orders you want to send. New products are introduced as the rounds progress, and they carry forward, so by Round 5 you are managing dozens of books at once.
- **Manual trading** — a one-off optimisation puzzle each round (auctions, expected-value games, allocation problems) scored separately.

We finished **23rd out of ~18,000 participants** — top ~0.1% — after a final-round comeback from 168th.

---

## My team | Bastion

Our team was spread across the world, with members from Canada, Australia, Croatia, and South Korea. So during the challenge it meant working across time zones, late-night calls, and a lot of messy but fun strategy discussions.

The competition definitely did not go smoothly for us. After Round 3 we were sitting at 126th, and Round 4 knocked us down even further to 168th. At that point we had to step back, figure out what was actually going wrong, and make the most of the final round.

Round 5 ended up being our comeback. We cleaned up our strategy, stayed locked in, and jumped all the way from 168th to 23rd overall. After being that far down, finishing in the top 25 felt insane, and it made the whole competition a lot more memorable.

| Member | LinkedIn |
| --- | --- |
| Mark Paje | https://www.linkedin.com/in/mark-paje-287270202/ |
| Asung Kil | https://www.linkedin.com/in/asung-kil-48393628b/ |
| Ante Cubela | https://www.linkedin.com/in/ante-1998-cubela/ |
| Woochan Im | https://www.linkedin.com/in/woochan-im-b0aa96386/ |
| Deepjot Grewal | https://www.linkedin.com/in/deepjot-grewal/ |

---

## Tools

A big part of our edge came from the tooling we built around the competition rather than any single strategy.

**Backtester.** At the start of each round we pulled the data capsule (training prices and trades) and downloaded the empty-strategy log, which let us reconstruct the exact dataset the website scores against. The result was a local backtester that reproduced the website PnL almost one-to-one. That gave us an effectively unlimited, website-faithful validation loop — we could test any number of strategies locally instead of burning real submissions, and we always separated train and test data to guard against overfitting.

**Visualiser.** Used mostly for PnL decomposition and for sanity-checking that strategies were doing what we expected. It also let us filter by individual counterparty bot — looking at a specific bot's buys and sells, and the post-trade price drift after they traded — which became important once we started trying to classify informed versus uninformed flow.

A recurring lesson across rounds: **the backtester reported PnL correctly, but our visualiser could show positions that exceeded the limit.** That discrepancy more than once made us double-check our order logic and catch real position-management bugs before they hit a submission.

---

## Round 1 & 2 — Warming up with the fundamentals

Rounds 1 and 2 traded the same two products: **Intarian Pepper Root** and **Ash Coated Osmium**. The two rounds were essentially the same problem — by Round 2 most serious teams were already over the qualification threshold, and our code barely changed between them.

### Intarian Pepper Root — a known drift

Pepper Root has an almost perfectly linear fair value that grows by ~1,000 per day. We modelled the fair price directly:

```
fair = round(start_mid / 1000) * 1000 + 0.001 × timestamp
```

Because the drift dominates the spread, the right move is to just get long and stay long:

- **Sweep to a core position of 70** immediately (no price filter — the upward drift more than pays for crossing the spread).
- **Opportunistically buy** any ask at or below fair.
- **Trim** above a soft cap of 75, and floor our passive ask at `fair + 3` so we never sell below fair.

On top of this we exploited a quirk we called the **"dummy taker."** Occasionally a hidden bot would lift resting orders when one side of the book went empty. By posting deep safety-net orders whenever a side was missing, we captured those fills for free. We grid-searched the safe thresholds on the website (successful values were ~89–91 for Osmium and ~112 for Pepper) and dialled them back slightly to avoid ever missing one.

Net result: a positively-biased buy-and-hold plus market-making book. We tried more sophisticated market making repeatedly and could never reliably beat simple "buy and hold the drift."

### Ash Coated Osmium — mean reversion around 10,000

Osmium trades around a fair value of 10,000 with a tight spread and small deviations. We ran a **z-score mean-reversion** strategy against the *fixed* 10,000 anchor (rather than a rolling mean, which we found noisier):

- Compute a rolling-window std, then `z = (mid − 10000) / std`.
- Set a **target position proportional to −z** — lean short when rich, long when cheap.
- Take asks when we're below target and the ask is below fair; take bids when we're above target and the bid is above fair.
- Continuously market make at `best_bid + 1` / `best_ask − 1`, with the same empty-book safety net.

### Manual — the clearing-price auction

The Round 1 manual was a sealed-bid auction puzzle (Dryland Flax and Ember Mushroom) where the exchange clears at a single price that maximises traded volume. Working through the mechanics by hand:

- **Dryland Flax:** bid 9,999 units at 30 → clears at 29 → profit **9,999**.
- **Ember Mushroom:** bid 19,999 units at 17 → clears at 16, net of the 0.1 fee → profit **77,996.1**.
- **Total: 87,995.1**, which several of us independently confirmed.

### Results

| | Placing | Algo PnL | Manual PnL |
| --- | --- | --- | --- |
| **Round 1** | 27th | 115,423 (27th) | 87,995 (~1st, hundreds tied) |
| **Round 2** | 14th | 112,526 (14th) | 200,716 (81st) |

---

## Round 3 & 4 — Options, options, options: to realise, or not to realise

Rounds 3 and 4 introduced an options complex: an underlying (**Velvetfruit Extract**), a near-static product (**Hydrogel Pack**, ~10,000), and a ladder of vouchers (**VEV** strikes from 4000 to 6500). This was the trickiest stretch of the competition for us.

The headline tension: there was a clear discrepancy between **implied volatility across strikes (~23%)** and **realised volatility (~32–41%)**. On paper that's a fat edge. In practice, the bid-ask spread relative to the product price was wide, order-book depth was thin, and transaction costs ate the theoretical profit alive.

<img width="1760" height="660" alt="image" src="https://github.com/user-attachments/assets/e9922efe-c7d1-45f0-8ea2-02094409122f" />

<img width="1320" height="660" alt="image" src="https://github.com/user-attachments/assets/9bf9ff72-015f-421d-8e47-e7510304857b" />

We spent real effort trying to make options theory pay:

> "It seems like Hedgehogs-style IV scalping on options is not that profitable when compared to last year. Maybe it's because the underlying's realised volatility is smaller and the bid-ask spread (relative to the product price) is wider than last year..."

> "I tried delta-hedged scalping and it turned out not to be profitable... maybe bid-ask spread and low order-book quantity was the issue."

We built proper machinery for it — Black-Scholes for the vanillas, closed-form pricing for the binary and chooser contracts, and a Monte Carlo simulator for the knock-out put. We then replayed our chosen orders through the challenge's own path generator (GBM, `S0 = 50`, `σ = 251%`, 252 trading days, 4 steps/day) and averaged realised PnL over **200,000 simulated paths** to separate model-implied EV from robust realised EV.

The honest conclusion: **the option theory didn't have an exploitable edge after costs.** So we pivoted to treating the whole complex as a set of mean-reverting and pair-tradeable spreads, and bet on that:

- **Hydrogel Pack** and **Velvetfruit Extract**: binary mean reversion around fixed fairs (~10,000 and ~5,257).
- **HG / VF pair fusion**: trade the spread between the two as a pair (entry/exit on spread deviation).
- **VEV 4000 / 4500**: binary option mean reversion driven by the VF deviation signal.
- **VEV 5000–5500**: ratio-trader-style directional positioning off the HG/VF spread.
- **VEV 6000 / 6500**: a standing bid at price 0 to scoop any free extrinsic value.
- IV scalping was switched **off**, and we ran in a mean-reversion-only mode.

The big strategic call — and in hindsight a debatable one — was to stop hedging our bets and **go all-in on the mean-reversion signal across every product.** If the products mean-revert, we win big; if they don't, we lose big. We accepted the variance because the entire edge rested on that one assumption.

For **Round 4** we kept the same architecture and let an autonomous tuning loop grind it (an "auto-cycle" that proposes a change, backtests, keeps it if a hard metric improved, reverts if not, and logs everything). The notable additions:

- **Counterparty modelling.** We classified the bots by post-trade drift. One bot's VF *buys* were consistently informed (positive drift, large t-stat) so we followed them; two others' *sells* were consistently uninformed so we faded them. These signals were folded directly into our fair-value deviation.
- **Time-to-expiry awareness.** Voucher entry thresholds scaled with remaining time to expiry, plus an end-of-day taper.

This put us comfortably in the top 15–20% of the round's PnL distribution — but we were also very aware that 100k+ PnL on this round usually meant overfitting, and 200k+ was confirmed overfit. Round 4 is where the field out-optimised us and we slid down the table.

The biggest recurring engineering problem in these rounds was **position-limit management.** Because the backtester would happily report PnL on positions that secretly exceeded the limit, we had to obsessively verify that no code path ever sent orders that would cross the cap. (Ante drove a lot of the strategy design but doesn't write code by hand — so the workflow was vibe-coding ideas with an LLM, then Kil reading through every final submission to verify the order logic was airtight.)

### Results

| | Placing | Algo PnL | Manual PnL |
| --- | --- | --- | --- |
| **Round 3** | 129th | 170,822 (127th) | 74,142 (297th) |
| **Round 4** | 162nd | 112,175 (417th) | 31,617 (612th) |

---

## Round 5 — The upset round

Round 5 changed everything. It dropped **~50 products at once** — Galaxy Sounds, Microchips, Oxygen Shakes, Panels, Pebbles, Robots, Sleep Pods, Snackpacks, Translators, UV Visors — each with a small position limit of ±10. The sheer breadth reset the playing field: there was no single clever model to find, just a lot of independent books to handle well.

Sitting at 168th, we rebuilt around a layered system with a clear priority order. Each product was routed to whichever layer suited it best:

- **Base strategy.** An EMA/std opportunistic taker (buy when the ask falls below `EMA − edge`, sell when the bid rises above `EMA + edge`) plus standing two-sided market making at `best_bid + 1` / `best_ask − 1`.
- **"Optimal MM" override.** For a set of products where pure, position-ignorant market making beat everything else in the data, we skipped all other logic and just quoted inside the spread.
- **Galaxy lead-lag overlay.** The five Galaxy Sounds products move together. We built a lead-lag signal from the Black Holes − Planetary Rings spread (fast EMA vs. slow EMA, z-scored) and used it to skew market-making size across the whole basket toward the predicted direction.
- **Planetary Rings trend-regime filter.** Planetary Rings kept catching a falling knife, so we gated it by trend: suppress buy-side quoting and flatten longs faster in a downtrend, quote both sides only when sideways.
- **Pair and single-product mean reversion.** A Chocolate−Vanilla snackpack spread z-score traded Vanilla against Chocolate; several products (Panel 1×2, Panel 4×4, Robot Vacuuming, UV Visor Magenta) ran their own mid-vs-EMA mean-reversion overrides.
- **"Beast mode" — discrete-jump scalping.** This is the one that mattered. A generic detector watches for a product whose mid sits *perfectly flat* for several ticks and then **jumps by more than 30 in a single tick** — a "rectangular" step. On an up-jump it fades short to −10 at the bid; on a down-jump it fades long to +10 at the ask. Health counters and a loss-streak cutoff stop it from fading a genuine trend.

Here's what actually happened, in our own words after results came out:

> "Our mean-reversion / EMA strategies turned out roughly 50/50, and we were negative on half the products. What happened was the scalping strat appeared on Oxygen Shake Chocolate and got us 489k (our total PnL was 522k). It was a good call to implement scalping on any product if we detect it — we found the strat, and had some luck that it appeared for an entire day on the test."

So the honest version of the comeback is two things at once: a **good design decision** (build a general detector that scalps the rectangular-jump pattern on *any* product, rather than hard-coding it to one) and a **slice of luck** (the pattern happened to run on Oxygen Shake Chocolate for the entire scored day). 489k of our 522k algo PnL came from that single behaviour firing on one product.

That was enough to vault us from 168th to **23rd overall.** Further inspecting the profit distribution amongst all the teams, our experience of having most of our deployed strategies being noise was not unique; the median team had essentially profited 0 XIRECS in this final round, with the average team losing quite a bit! 

<img width="1770" height="948" alt="image" src="https://github.com/user-attachments/assets/b7bccd68-a8c6-4aaa-84ea-9926b383ab26" />

### Results

| | Placing | Algo PnL | Manual PnL |
| --- | --- | --- | --- |
| **Round 5** | 23rd (956,286 cumulative) | 522,477 (38th) | 45,054 (1361st) |

---

## Final standing

| Round | Round placing | Cumulative standing |
| --- | --- | --- |
| 1 | 27th | 27th |
| 2 | 14th | 14th |
| 3 | 129th | 126th |
| 4 | 162nd | 168th |
| 5 | **23rd** | **23rd** |

From 168th to 23rd in a single round, finishing in the top ~0.1% of ~18,000 teams.

---

## Gen AI use — a vibe, or not?

With the speed at which AI tooling has progressed, we went in expecting Prosperity 4's difficulty to assume heavy AI usage. Our team leaned on ChatGPT and Claude throughout — for script-writing, signal ideation, and debugging.

The honest read on where AI helped and where it didn't:

- **Where it helped:** building and maintaining tooling (backtester, visualiser, order-management plumbing), running large parameter sweeps, classifying counterparty behaviour at scale, and acting as a tireless reviewer for position-limit correctness on every final submission. It was excellent at the *infrastructure* and the *grind*.
- **Where it didn't:** the directional calls. The ideas that worked came from prior-year knowledge, top-team repos, and human judgement about what was worth betting on (going all-in on mean reversion in R3/R4, building a *general* jump-scalper in R5). AI couldn't tell us which assumptions about the market were true — only the data could, and only after we'd decided what to test.

### Asung Kil — our AI lead

**Tooling.** My main driver was Claude Code, plus Andrej Karpathy's autoresearch loop for autonomous experimentation. Most of the work — data analysis, backtesting, and tuning — went through the agent.

**Setting up a fast local feedback loop.** At the start of each round I pulled the data capsule (the training prices/trades) and downloaded the empty-strategy log, from which I could reconstruct the exact dataset the website scores on, so our local backtester reproduced the website PnL almost one-to-one. That gave me an unlimited, website-faithful validation loop — I could test any number of strategies locally instead of spending real submissions, and always separated train/test data to prevent overfitting.

**Idea → data-driven selection.** I seeded each round with naive strategy hypotheses drawn from last year's competition and the public repos of past top teams. I'd hand some raw ideas to the agent and have it run the data analysis (per-product volatility, correlations, mean-reversion, bot-trade patterns, etc.) and tell me which hypotheses the data actually supported. So the agent's role here was to filter and shape the overall strategy direction, not just to code it up.

**Tuning as quantitative optimisation.** Once a direction was set, the detailed design and parameter tuning was all metric-driven: I had the agent run the backtester and optimise hyperparameters to maximise hard metrics like expectancy/PnL and Sharpe, using the gap between training and the website-equivalent set as the overfit guard.

**Autonomous research loop.** For the heavier exploration I leaned on the autoresearch pattern — the agent edits the strategy, runs the backtest, keeps the change if the metric improved and reverts if not, then repeats and logs every experiment. I scheduled this to run cycles on its own (pull the latest intel → propose a tweak → backtest → keep/discard → log), so a lot of the incremental gains accumulated while I was away.

**Net effect.** The directional ideas came from prior-year knowledge and top-team repos, but the agent did the heavy lifting: proving which ideas the data supported, building a website-faithful local validation loop, and autonomously grinding the parameter space against hard metrics.

---

## What we took away

- **Simple beats clever when the structure is simple.** Pepper Root's drift was best captured by buy-and-hold; every attempt to be cleverer left money on the table.
- **A theoretical edge isn't an edge after costs.** The options vol mispricing in R3/R4 was real on paper and untradeable in practice. Knowing when *not* to trade a signal is its own skill.
- **A website-faithful backtest is worth more than any single strategy.** Reconstructing the scoring dataset locally was probably our single highest-leverage piece of work.
- **Generality plus a little luck.** The R5 comeback came from building a detector that could scalp a pattern on *any* product rather than overfitting to one — and then getting fortunate about where that pattern showed up. We'll happily take both.
