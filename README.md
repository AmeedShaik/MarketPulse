# MarketPulse – Live XAUUSD & BTCUSD News Dashboard

## Run
* **Windows:** double-click `run.bat` → the browser opens at http://localhost:8765
* **Other:** `python server.py`

You only need Python 3.9+. Everything uses the standard library, so there's nothing to `pip install`.

## What it shows
| Panel | Content | Refresh |
|---|---|---|
| Ticker | Gold (GC=F), BTC, DXY, US10Y, WTI, Silver, S&P/Nasdaq futures, VIX, Gold/Silver ratio, Crypto Fear & Greed, BTC funding | 15–60 s |
| Asset cards | Today's sentiment score (−100…+100) = 45% headlines + 40% technicals + 15% DXY/yields; top headline drivers; nearest S/R; ATR expected range; RSI | 15 s |
| S/R ladder | Daily & weekly classic pivots, prev day/week H-L, EMA 20/50/200, swing-cluster levels | 15 s |
| News wire | 13 feeds (Google News, CoinDesk, Cointelegraph, Decrypt, FXStreet, ForexLive, Investing.com, CNBC, Federal Reserve press & speeches). Tagged GOLD/BTC/MACRO/FED, impact level, per-asset ▲▼ score | 30 s |
| Economic calendar | USD events + global high-impact events (Fed speakers, PMI, CPI, NFP, GDP…), countdown to the next high-impact USD release, volatility warning inside 30 min | 15 min |
| Alerts | 🔔 sound and 🖥 desktop notification when a new HIGH-impact or Fed headline arrives | live |
| Charts | Embedded TradingView OANDA:XAUUSD / BITSTAMP:BTCUSD charts (15m) | live |

## Customise (top of `server.py`)
* `NEWS_REFRESH_SEC`, `PRICE_REFRESH_SEC` set the polling speed.
* `ASSETS` sets the price source. Gold uses `GC=F` (COMEX futures), with Binance `PAXGUSDT` as a fallback.
* `FEEDS` lets you add or remove RSS feeds.
* `LEX_XAU`, `LEX_BTC`, `GEN_BULL`, `GEN_BEAR` are the sentiment keywords.

## Notes
* The source-health panel shows each feed's status. Some sites, such as Investing.com, sometimes block automated requests; when that happens the other feeds keep working.
* The economic calendar comes from the free Forex Factory JSON feed and is cached in `calendar_cache.json`.
* Sentiment comes from a keyword-plus-technicals model. Treat it as a decision aid, not as a trading signal.
