# 🛒 Shufersal Weekly Planner

Tell it your family size, weekly budget and food rules. It pulls **today's real prices and deals** for your Shufersal branch, then uses Claude to build a 7-day meal plan and a shopping list that's priced line by line and kept under budget.

## Run it

```bash
cp .env.example .env        # then paste your Anthropic API key into .env
python3 app.py              # opens http://localhost:8765
```

Or double-click `run.command` in Finder. Needs Python 3.9+ only. No packages to install.

## Where the data comes from

Israel's food price transparency law requires every chain to publish its full price list and every active promotion, per branch, as XML. Shufersal posts them at <https://prices.shufersal.co.il>:

| File | What's in it |
|---|---|
| `PriceFull` | Every product at the branch: barcode, name, size, shelf price, price per unit |
| `PromoFull` | Every deal: "2 for ₪25", single price, % off, start and end dates, club-only flag |
| `Stores` | The branch list (fills the dropdown) |

Files are cached in `cache/` for 6 hours.

## How a plan is made

1. **Deals → effective prices.** "4 for ₪10" becomes ₪2.50 a unit. The planner skips coupons, expired deals, club-only deals (unless you tick the box), minimum-spend deals, brand-wide blanket discounts, and "2 for ₪X" deals on by-weight items (those are per pack, not per kg).
2. **Catalogue.** It takes the ~350 best food deals, ranked by % saved, and adds the 3 cheapest options for about 75 staples (vegetables by the kg, chicken, eggs, dairy, bread, rice and so on). Non-food items are filtered out.
3. **Claude plans.** It gets your inputs plus the catalogue and returns a meal plan with a shopping list made of exact product codes.
4. **Real prices.** Every line is re-priced from the XML, deal maths included. Claude's own arithmetic is never used. If the total is over budget, or a code doesn't exist, Claude gets that feedback and tries again, up to 3 times.

## Files

- `shufersal.py`: download, parse, cache and deal maths
- `planner.py`: catalogue, prompt, Claude API call, pricing and the retry loop
- `app.py`: local web server (stdlib only)
- `static/index.html`: the form and the results
- `tests/`: `python3 -m unittest discover tests`

## Known limits (v1.0)

- Weighted produce quantities are in kg. The real bill will be a little different from the estimate.
- An item can be on the price list but out of stock at the shop.
- The non-food filter works by keyword, so the odd soap may still get through.
