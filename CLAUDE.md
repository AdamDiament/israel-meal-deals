# israel-meal-deals

A local web app that plans a family's weekly groceries and meals from **live Shufersal prices and deals**, kept under a budget. Owner: Adam (Jerusalem). The default branch is 2, שלי ירושלים- אגרון.

## Run / test
- `python3 app.py` serves http://localhost:8765 with hot reload: editing a `.py` file restarts the server, and editing `static/index.html` makes the page refresh itself. Add `--no-reload` to turn that off.
- `python3 -m unittest discover tests`. Run this after every change.
- Python 3.9+ standard library only. **No pip dependencies.** Keep it that way.

## Architecture
- `shufersal.py`: downloads the price-transparency XML (gzipped, from `prices.shufersal.co.il` listing pages → Azure blob links), parses it and caches it in `cache/` for 6h. Categories: 2 = PriceFull, 4 = PromoFull, 5 = Stores.
  - Deal maths: `DiscountedPrice` is the total for `MinQty` units, so the effective unit price is `DiscountedPrice / MinQty`. This was checked on live files ("2ב25" → MinQty 2, price 25).
  - Skipped: coupons, gifts, expired deals, club-only deals (unless the toggle is on), min-spend deals, promos covering more than 30 items (brand-wide), and multi-pack deals on weighted items (those are per pack, not per kg).
  - `line_cost()` prices a quantity, applying the deal only in full multiples of `MinQty`.
- `planner.py`: builds a catalogue (about 350 top food deals plus the 3 cheapest of about 75 staples, with loose produce sold by kg first), filters non-food with the `NON_FOOD` regex, writes the prompt, and calls Claude.
  - Backend "plan" (the default) runs `claude -p --output-format json` through a subprocess, which uses Adam's Claude subscription. `ANTHROPIC_API_KEY` is removed from the subprocess environment on purpose so it never bills the API. Backend "api" is the optional paid API.
  - **Never trust the model's arithmetic:** `price_list()` re-prices every line from the XML. If the total is over budget or a code is unknown, the feedback goes back to Claude, for up to 3 attempts.
- `app.py`: stdlib `ThreadingHTTPServer`. Endpoints: `/api/stores`, `/api/status`, `/api/rev` (hot reload), POST `/api/plan`.
- `static/index.html`: a single file holding the form and results, vanilla JS. Form values are saved in localStorage.

## Adam's preferences
- Recipe units: whole counts for produce (e.g. "1 medium zucchini"), grams for dry goods, spoons for small amounts and oil, round numbers. Never awkward conversions like 236.6 ml.
- Defaults: kosher-style (no meat with dairy), each dinner cooked as a double batch that covers two nights.
- Bump `VERSION` in `app.py` for user-visible changes. Commit with clear messages and push to `main`.
