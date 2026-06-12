# IMC Prosperity 4

IMC Prosperity 4, 2026 edition, was a global trading competition comprising of thousands of different teams. 

## Tools

- Backtester


- Visualiser 
Used for PNL decomp mostly, and to check if our strategies were aligned with what we were expecting. 


## My team | Bastion

Our team was spread across the world, with members from Canada, Australia, Croatia, and South Korea. So during the challange it meant working across time zones, late-night calls, and a lot of messy but fun strategy discussions.

The competition definitely did not go smoothly for us. After Round 3, we were sitting at 126th, and Round 4 knocked us down even further to 168th. At that point, we had to step back, figure out what was actually going wrong, and make the most of the final round.

Round 5 ended up being our comeback. We cleaned up our strategy, stayed locked in, and jumped all the way from 168th to 23rd overall. After being that far down, finishing in the top 25 felt insane, and it made the whole competition a lot more memorable.

Mark Paje - https://www.linkedin.com/in/mark-paje-287270202/ 
Asung Kil - https://www.linkedin.com/in/asung-kil-48393628b/ 
Ante Cubela - https://www.linkedin.com/in/ante-1998-cubela/ 
Woochan Im - https://www.linkedin.com/in/woochan-im-b0aa96386/
Deepjot Grewal - https://www.linkedin.com/in/deepjot-grewal/

## Round 1 and 2 - Warming up with the fundamentals.

[ overview of the round]

Rounds 1 and 2 were essentially the same, trading the same two products, Intarian Pepper Root and Ash Coated Osmium. 

Pepper - linear function, grew by 1,000 each day

- Buy and hold seemed to be our best strategy for this product. Market making 

Ash - Fair price reverting around 10,000. 

First round - 27th 
115,423 + (Algo), 27th
87,995 + (Manual), 1st (Hundreds of teams tied for first) 

Second round (Not many people tried as hard as alot of teams were already over the threshold, nothing changed between the rounds anyway) - 14th 

112,526 + (Algo), 14th
200,716 + (Manual), 81st 
----

## Round 3 and 4 - Options, options, options - to realise, or to not to realise. 

tricky round because there was a discrepency between implied volatility across strikes (23%) and realised volatility (32%% to 41%), however trying to cash in on this was difficult due to transaction costs. 

<img width="1760" height="660" alt="image" src="https://github.com/user-attachments/assets/e9922efe-c7d1-45f0-8ea2-02094409122f" />
 
<img width="1320" height="660" alt="image" src="https://github.com/user-attachments/assets/9bf9ff72-015f-421d-8e47-e7510304857b" />

"It seems like Hedgehogs-style IV scalping on options is not that profitable when compared to last year. Maybe it's because the underlying's realized volatility is smaller and bid-ask spread (relative to the product price) is wider than last year..."

"I tried delta hedged scalping and turned out not to be profitable... maybe bidask spread and low orderbook quantity was an issue. So I think mean-reversion for deep ITM and IV scalping for ATM is near the optimal."

one of the main things in round 3 was deciding between a 'risk on' vs 'risk off' behaviour with a mean-reversion strategy. 


round 3 placing - 129th 
170,822 + (algo), 127th ranking
74,142 + (manual), 297th ranking

round 4 placing - 162nd
112,175 + (algo), 417th ranking
31,617 + (manual), 612th

## Round 5 - The Upset round!

round 5 placing - 23rd (956,286)
522,477 + (algo), 38th ranking
45,054 + (manual), 1361st


## Gen AI use - A vibe or not?

With the amazingly fast progression of AI tools, software, libraries and agents, we expected Prosperity 4's difficulty to reflect assumed usage of AI. Our team used ChatGPT and Claude for numerous tasks, such as writing script-writing, signal ideation, and debugging. 

[ Describe limits and the advantages of using AI in this comp ]

Our AI lead, Asung Kil, 

Tooling. My main driver was Claude Code, plus Andrej Karpathy's autoresearch loop for autonomous experimentation. Most of the work — data analysis, backtesting, and tuning — went through the agent.

Setting up a fast local feedback loop. At the start of each round I pulled the data capsule (the training prices/trades) and downloaded empty-strategy log. From which I could reconstruct the exact dataset the website scores on, so our local backtester reproduced the website PnL almost one-to-one. That gave me an unlimited, website-faithful validation loop — I could test any number of strategies locally instead of spending real submissions, and always separated train/test data to prevent overfitting. 

Idea → data-driven selection. I seeded each round with naive strategy hypotheses drawn from last year's competition and the public repos of past top teams. I'd hand some raw ideas to the agent and have it run the data analysis (per-product volatility, correlations, mean-reversion, bot-trade patterns, etc.) and tell me which hypotheses the data actually supported. So the agent's role here was to filter and shape the overall strategy direction, not just to code it up.

Tuning as quantitative optimization. Once a direction was set, the detailed design and parameter tuning was all metric-driven: I had the agent run the backtester and optimize hyperparameters to maximize hard metrics like expectancy/PnL and Sharpe, using the gap between training and the website-equivalent set as the overfit guard.

Autonomous research loop. For the heavier exploration I leaned on the autoresearch pattern — the agent edits the strategy, runs the backtest, keeps the change if the metric improved and reverts if not, then repeats and logs every experiment. I scheduled this to run cycles on its own (pull the latest intel → propose a tweak → backtest → keep/discard → log), so a lot of the incremental gains accumulated while I was away.

Net effect. The directional ideas came from prior-year knowledge and top-team repos, but the agent did the heavy lifting: proving which ideas the data supported, building a website-faithful local validation loop, and autonomously grinding the parameter space against hard metrics.