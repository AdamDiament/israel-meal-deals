"""Turn branch prices + deals into a budgeted weekly meal plan via Claude."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request

from shufersal import line_cost

DEFAULT_MODEL = "claude-sonnet-5-5"
API_URL = "https://api.anthropic.com/v1/messages"

# Non-food words: anything matching is dropped from the candidate list.
NON_FOOD = re.compile(
    "שמפו|מרכך|סבון|תחליב|ג'ל רחצה|גל רחצה|נייר|מגבות|מגבונ|טיטול|חיתול|האגיס|פמפרס|"
    "כביסה|מרכך כביסה|אקונומיקה|ניקוי|מנקה|ספוג|כוסות|צלחות|סכו\"ם|מזלג|כפיות|שקיות|ניילון|"
    "אלומיניום|תבנית|נרות|גפרור|סוללות|דאודורנט|משחת שיניים|מברשת|בושם|קרם|לק |תער|סכין גילוח|"
    "אבקת|טבליות למדיח|מדיח|מטהר|מזון לחתול|מזון לכלב|לחתול|לכלבים|חול לחתול|תחבושות|"
    "ספריי|ספרי |הגנה מהשמש|רול און|רולאון|ת\\.רחצ|ת\\.חלב|ס\\.נוזלי|ש\\.|מ\\.כביסה|קרם |גילוח|פדים|טמפון|ויטמין|תוסף|קונדום|צעצוע|מצית|פח|כפפות|מטאטא|סיגרי|טבק|יין |וודקה|בירה|ויסקי|ערק"
)

# Staples Claude should always be able to price, even without a deal.
STAPLES = [
    # produce (mostly sold by kg)
    "עגבניה", "מלפפון", "בצל יבש", "תפוחי אדמה", "בטטה", "גזר", "פלפל אדום", "קישוא",
    "חציל", "כרוב", "חסה", "לימון", "שום", "בננה", "תפוח עץ", "תפוז", "קלמנטינה", "אבוקדו",
    "פטרוזיליה", "כוסברה", "שמיר", "ברוקולי", "כרובית", "פטריות",
    # protein
    "חזה עוף", "שוקיים", "כרעיים", "פרגית", "עוף שלם", "בשר טחון", "ביצים", "טונה",
    "פילה סלמון", "פילה אמנון", "טופו", "עדשים", "חומוס יבש", "שעועית",
    # dairy
    "חלב 3%", "חלב 1%", "קוטג", "גבינה לבנה", "יוגורט", "גבינה צהובה", "חמאה", "שמנת",
    "לבן", "בולגרית",
    # carbs / bakery
    "לחם", "פיתות", "חלה", "אורז", "פסטה", "ספגטי", "קוסקוס", "בורגול", "קוואקר",
    "פתיתים", "טורטיה", "קמח",
    # pantry
    "שמן קנולה", "שמן זית", "רסק עגבניות", "עגבניות מרוסקות", "טחינה", "סוכר", "מלח",
    "פפריקה", "כמון", "אבקת מרק", "דבש", "ריבה", "חמאת בוטנים", "שוקולד ממרח",
]

DOUBLE_DINNER = (
    "cook each dinner in a double batch that lasts TWO nights. Night 1 is the cooked meal; night 2 is "
    "'Leftovers: <dish>' (optionally with a quick fresh side). So 7 days = 4 cooked dinners, the last one "
    "covering one night or reaching into next week. Size the dinner quantities for two nights and write the "
    "recipe quantities for the full double batch. Pick dishes that keep and reheat well."
)

UNITS_STYLE = (
    "Recipe units: whole counts for produce (e.g. '1 medium zucchini', optionally with grams), "
    "grams for dry ingredients, tablespoons/teaspoons for small amounts and oil, round numbers "
    "throughout. Never awkward conversions like 236.6 ml."
)


def _fmt(item: dict, deal: dict | None) -> str:
    unit = "kg" if item["weighted"] else "unit"
    s = f'{item["code"]}|{item["name"]}|{item["price"]:.2f}/{unit}'
    if deal:
        s += f'|DEAL {deal["desc"]} → {deal["eff_unit"]:.2f}/{unit}'
        if deal["min_qty"] > 1 and not item["weighted"]:
            s += f' (buy {deal["min_qty"]:g})'
        s += f' until {deal["ends"]}'
    return s


def build_candidates(prices: dict, promos: dict, max_deals: int = 350, per_staple: int = 3):
    """Compact product catalogue for the prompt: best food deals + cheapest staples."""
    deals = []
    for code, d in promos.items():
        it = prices[code]
        if NON_FOOD.search(it["name"]) or NON_FOOD.search(d["desc"]):
            continue
        saving = (it["price"] - d["eff_unit"]) / it["price"]
        deals.append((saving, code))
    deals.sort(reverse=True)
    chosen = [c for _, c in deals[:max_deals]]
    seen = set(chosen)

    for kw in STAPLES:
        matches = [it for it in prices.values()
                   if kw in it["name"] and it["code"] not in seen and not NON_FOOD.search(it["name"])]
        # cheapest by unit price where available (per kg / per 100g), else shelf price
        # loose produce/meat sold by kg first, then cheapest by unit price
        matches.sort(key=lambda it: (not it["weighted"], it["unit_price"] or it["price"]))
        for it in matches[:per_staple]:
            chosen.append(it["code"])
            seen.add(it["code"])

    return [(c, _fmt(prices[c], promos.get(c))) for c in chosen]


def build_prompt(params: dict, catalogue_lines: list[str], feedback: str = "") -> str:
    meals = ", ".join(params.get("meals") or ["breakfast", "lunch", "dinner"])
    pantry_have = params.get("pantry_have") or "nothing specified"
    lang = params.get("language", "English")
    p = f"""You are planning a week of groceries from ONE Shufersal branch for a family.

FAMILY: {params.get('adults', 2)} adults, {params.get('kids', 0)} children (ages: {params.get('kid_ages') or 'n/a'})
BUDGET: ₪{params['budget']} maximum for the whole shop (hard limit).
DAYS: {params.get('days', 7)}. MEALS TO COVER EACH DAY: {meals}.
DIETARY RULES: {params.get('diet') or 'none'}
DISLIKES / AVOID: {params.get('avoid') or 'none'}
MUST INCLUDE: {params.get('must') or 'nothing specific'}
ALREADY AT HOME (do NOT buy these, but you may cook with them): {pantry_have}
ASSUME BASIC PANTRY (salt, pepper, common spices, oil, sugar, flour) AT HOME: {'yes' if params.get('basic_pantry', True) else 'no — buy anything needed'}
DINNER STRATEGY: {DOUBLE_DINNER if params.get('double_dinners', True) else 'cook a fresh dinner each night'}
EXTRA NOTES: {params.get('notes') or 'none'}

PRODUCT CATALOGUE (code|name|shelf price|optional deal). Weighted items are priced per kg.
Only buy products from this list, by exact code.
{chr(10).join(catalogue_lines)}

HOW TO PLAN
- Build meals around the deals first; use staples to fill gaps. Reuse ingredients across days to cut waste.
- Quantities must be realistic for the family size and number of days.
- For a "buy N" deal, buy a multiple of N if you want the deal price.
- Weighted items: qty is in kg (e.g. 1.5). Other items: qty is a whole number of packs.
- Stay under the budget; aim for roughly 90–100% of it unless the family's needs are met for less.
- {UNITS_STYLE}
- Write meal names, recipes and notes in {lang}. Keep product names exactly as given (Hebrew).
{feedback}
Do not use any tools or read any files; everything you need is above.
Respond with ONLY a JSON object, no markdown fences:
{{
  "summary": "1–2 sentences on the week's approach",
  "shopping_list": [{{"code": "...", "qty": 2, "for": "which meals"}}],
  "meal_plan": [{{"day": "Sunday", "meals": [{{"meal": "dinner", "name": "...", "recipe": "short method with quantities"}}]}}],
  "prep_tips": ["..."],
  "pantry_used": ["home items the plan relies on"]
}}"""
    return p


def call_claude(prompt: str, api_key: str, model: str = DEFAULT_MODEL, max_tokens: int = 16000) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Claude API error {e.code}: {e.read().decode(errors='replace')[:500]}")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


# ---------------------------------------------------------------- Claude Code (uses your Claude plan)

CLI_PATHS = ["~/.claude/local/claude", "~/.local/bin/claude", "/opt/homebrew/bin/claude",
             "/usr/local/bin/claude", "~/.npm-global/bin/claude"]


def find_cli() -> str:
    found = shutil.which("claude")
    if found:
        return found
    for p in CLI_PATHS:
        p = os.path.expanduser(p)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # nvm installs live under ~/.nvm/versions/node/*/bin
    nvm = os.path.expanduser("~/.nvm/versions/node")
    if os.path.isdir(nvm):
        for v in sorted(os.listdir(nvm), reverse=True):
            p = os.path.join(nvm, v, "bin", "claude")
            if os.access(p, os.X_OK):
                return p
    return ""


def call_claude_cli(prompt: str, model: str = "") -> str:
    """Run Claude Code headless. Billed to your Claude subscription, not the API."""
    exe = find_cli()
    if not exe:
        raise RuntimeError("Claude Code isn't installed (no `claude` command found). Install it, "
                           "run `claude` once to log in with your Claude account, then try again.")
    env = dict(os.environ)
    # If an API key is in the environment, Claude Code would bill the API instead of your plan.
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, timeout=900,
                       cwd=os.path.dirname(os.path.abspath(__file__)))
    out = r.stdout.strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = None
    if data is None:
        if r.returncode != 0:
            raise RuntimeError(f"Claude Code failed: {(r.stderr or out).strip()[:800]}")
        return out
    if r.returncode != 0 or data.get("is_error"):
        reason = data.get("result") or data.get("error") or data.get("subtype") or r.stderr.strip()
        hint = ""
        low = str(reason).lower()
        if "login" in low or "api key" in low or "auth" in low:
            hint = " — open Terminal, run `claude`, and type /login to sign in with your Claude account."
        elif "limit" in low:
            hint = " — you've hit your plan's usage limit; try again after it resets."
        raise RuntimeError(f"Claude Code error: {str(reason)[:800]}{hint}")
    return data.get("result", "")


def ask_claude(prompt: str, params: dict) -> str:
    backend = params.get("backend") or "plan"
    if backend == "api":
        key = get_api_key(params)
        if not key:
            raise RuntimeError("No Anthropic API key. Paste one in Settings, put ANTHROPIC_API_KEY=... in .env, "
                               "or switch the backend to 'My Claude plan'.")
        return call_claude(prompt, key, params.get("model") or DEFAULT_MODEL)
    return call_claude_cli(prompt, params.get("cli_model") or "sonnet")


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("Claude did not return JSON")
    return json.loads(text[start:end + 1])


def price_list(plan: dict, prices: dict, promos: dict) -> dict:
    """Recompute every line from real shelf prices + deals. Never trust the model's maths."""
    lines, unknown, total, saved = [], [], 0.0, 0.0
    for row in plan.get("shopping_list", []):
        code = str(row.get("code", "")).strip()
        item = prices.get(code)
        if not item:
            unknown.append(code)
            continue
        qty = float(row.get("qty") or 1)
        if not item["weighted"]:
            qty = max(1, round(qty))
        cost, applied = line_cost(item, promos.get(code), qty)
        full = round(item["price"] * qty, 2)
        total += cost
        saved += full - cost
        lines.append({
            "code": code, "name": item["name"], "brand": item["brand"],
            "qty": qty, "unit": "kg" if item["weighted"] else "",
            "unit_price": item["price"], "cost": cost,
            "deal": promos[code]["desc"] if applied else "",
            "for": row.get("for", ""),
        })
    return {"lines": lines, "total": round(total, 2), "saved": round(saved, 2), "unknown_codes": unknown}


def make_plan(params: dict, prices: dict, promos: dict, log=print) -> dict:
    budget = float(params["budget"])
    cat = build_candidates(prices, promos)
    lines = [l for _, l in cat]
    feedback, plan, priced = "", None, None
    for attempt in range(3):
        log(f"Asking Claude (attempt {attempt + 1}, {len(lines)} products)…")
        plan = parse_json(ask_claude(build_prompt(params, lines, feedback), params))
        priced = price_list(plan, prices, promos)
        over = priced["total"] - budget
        if over <= 0 and not priced["unknown_codes"]:
            break
        parts = []
        if over > 0:
            parts.append(f"Your previous list came to ₪{priced['total']:.2f} at real prices — ₪{over:.2f} OVER "
                         f"the ₪{budget:.0f} budget. Cut or swap items to get under it.")
        if priced["unknown_codes"]:
            parts.append(f"These codes are not in the catalogue, don't use them: {', '.join(priced['unknown_codes'])}.")
        feedback = "\nIMPORTANT — FIX FROM LAST TRY: " + " ".join(parts) + "\n"
    return {
        "summary": plan.get("summary", ""),
        "meal_plan": plan.get("meal_plan", []),
        "prep_tips": plan.get("prep_tips", []),
        "pantry_used": plan.get("pantry_used", []),
        "shopping": priced,
        "budget": budget,
        "within_budget": priced["total"] <= budget,
        "deals_considered": len(promos),
        "catalogue_size": len(lines),
    }


def get_api_key(params: dict) -> str:
    key = (params.get("api_key") or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env):
            for line in open(env, encoding="utf-8"):
                if line.strip().startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
    return key
