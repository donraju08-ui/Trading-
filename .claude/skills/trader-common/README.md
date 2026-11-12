# trader-common: switch the trader-* skills between the US market and the ASX

```bash
python3 .claude/skills/trader-common/market.py              # active market, both clocks, open or closed
python3 .claude/skills/trader-common/market.py use asx      # every skill on the ASX (writes trader/config.json)
python3 .claude/skills/trader-common/market.py use us       # back to NYSE / Nasdaq
python3 .claude/skills/trader-common/market.py show asx     # hours, holidays, defaults
```

One run on the other market: add `--market us` or `--market asx` to any skill's
command, or set `TRADER_MARKET`. Order: flag, then `TRADER_MARKET`, then
`"market"` in `trader/config.json`, then us.

| | US | ASX |
| --- | --- | --- |
| clock | New York (ET) | Sydney (AEST / AEDT) |
| session | 9:30 to 16:00, early close 13:00 | 10:00 to 16:00, early close 14:00 |
| holidays | NYSE 2026-2027 | ASX 2026-2027 (asx.com.au calendar) |
| tickers | TSLA, BRK.B | BHP or BHP.AX, filed everywhere as BHP.AX |
| currency | $ (USD) | A$ (AUD) |
| no_entries_before / after, flat_by | 09:45 / 15:30 / 15:55 | 10:15 / 15:30 / 15:55 |
| broker | Alpaca (default) or IBKR | Interactive Brokers only (Alpaca is refused) |
| news | Yahoo US headlines + SEC 8-Ks | Yahoo AU headlines; ASX announcements: read on asx.com.au |
| earnings analyst, Pelosi tracker | yes | US only (the analyst refuses on the ASX) |

Per-market settings: put them in a block named after the market, laid over the
top-level keys, e.g. `{"market": "asx", "budget": 5000, "asx": {"budget": 8000,
"broker": "ibkr", "no_entries_after": "15:00"}}`.

Limits on the ASX: Yahoo's free ASX bars are about 20 minutes delayed and have
gaps. The scanner allows up to 8 missing 5-minute bars a chart, and the time
machine fills up to 6 missing bars a session flat at the last close, and says
so. Printed prices say A$; JSON files keep plain numbers.
