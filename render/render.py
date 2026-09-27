import base64
import datetime as dt
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
PLATFORM = os.environ.get("STATS_PLATFORM") or ("github" if os.environ.get("GITHUB_ACTIONS") else "gitlab")
ROLE = os.environ.get("STATS_ROLE", "")
HIDE_PRIVATE = os.environ.get("STATS_HIDE_PRIVATE") == "1"
TZ = dt.timezone(dt.timedelta(hours=int(os.environ.get("STATS_TZ_HOURS", "5"))))
TZ_NAME = os.environ.get("STATS_TZ_NAME", "Astana")
CACHE = Path(os.environ.get("STATS_CACHE", HERE.parent / ".cache" / "diffs.json"))
WINDOW = 182

JP = {
    "chapter": "第話",
    "daily": "毎日の記録",
    "boom": "ドン！",
    "menace": "ゴ",
    "troupe": "幻影旅団",
    "bungee": "伸縮自在の愛",
    "job": "依頼完了",
    "stamp": "済",
}

INK, PAPER, RED, TONE, LIGHT, MUTE, RULE = "#141414", "#fbf8f1", "#d7263d", "#857f73", "#c9c1b0", "#6b665c", "#e4ddcd"
SPACED = 'letter-spacing=".14em"'
HALO6, HALO4 = 'stroke-width="6"', 'stroke-width="4"'
SERIES = [("commit", INK), ("mr", RED), ("review", TONE), ("private", LIGHT)]
UNTIMED = {"release", "private"}

LOCKFILES = r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock"
EXCLUDED = re.compile(
    rf"(^|/)({LOCKFILES})$|(^|/)migrations/\d{{4}}_[^/]*\.py$|\.min\.(js|css)$"
    r"|\.(map|snap|svg|png|jpe?g|gif|webp|ico|woff2?|ttf|otf|pdf)$"
)
TESTS = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[^/.]+$|\.(test|spec)\.[^/]+$")
LANGS = [
    ("Python", {".py", ".pyi"}),
    ("TypeScript", {".ts", ".tsx"}),
    ("JavaScript", {".js", ".jsx", ".mjs", ".cjs", ".vue"}),
    ("Go", {".go"}),
    ("Shell & infra", {".sh", ".bash", ".hcl", ".nomad", ".tf", "Dockerfile", "Justfile", "Makefile"}),
    ("Markup & styles", {".html", ".jinja", ".j2", ".css", ".scss", ".sass", ".less"}),
    ("Docs", {".md", ".rst", ".txt"}),
    ("Data & config", {".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".sql", ".csv", ".env"}),
]


def language(path):
    name = path.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    for lang, keys in LANGS:
        if ext in keys or name in keys:
            return lang
    return "Other"


def collect():
    if PLATFORM == "github":
        import source_github as source
    else:
        import source_gitlab as source
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    data = source.collect(cache)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({c["id"]: c["files"] for c in data["commits"]}))
    return data


def ranked_repos(data, acts):
    counts = defaultdict(Counter)
    for _, kind, key in acts:
        if kind not in UNTIMED:
            counts[key][kind] += 1
    rows = [(data["repos"][key]["name"], c, 1) for key, c in counts.items() if not HIDE_PRIVATE or data["repos"][key]["public"]]
    hidden = [c for key, c in counts.items() if HIDE_PRIVATE and not data["repos"][key]["public"]]
    if hidden:
        rows.append((f"private · {len(hidden)} repos", sum(hidden, Counter()), len(hidden)))
    return sorted(rows, key=lambda row: (-sum(row[1].values()), row[0]))


def summarize(data):
    now = dt.datetime.now(TZ)
    today = now.date()
    acts = [(t.astimezone(TZ), kind, key) for t, kind, key in data["acts"]]
    kinds = Counter(kind for _, kind, _ in acts)

    first = min(t.date() for t, _, _ in acts)
    start = max(first, today - dt.timedelta(days=WINDOW - 1))
    days = [start + dt.timedelta(days=i) for i in range((today - start).days + 1)]
    daily = {d: Counter() for d in days}
    for t, kind, _ in acts:
        if t.date() in daily:
            daily[t.date()][kind] += 1

    active = sorted({t.date() for t, _, _ in acts})
    longest = run = 1
    for a, b in pairwise(active):
        run = run + 1 if (b - a).days == 1 else 1
        longest = max(longest, run)
    current, cursor, active_set = 0, today if today in active else today - dt.timedelta(days=1), set(active)
    while cursor in active_set:
        current, cursor = current + 1, cursor - dt.timedelta(days=1)

    counted = [(t, kind) for t, kind, _ in acts if kind not in UNTIMED]
    hours = Counter(t.hour for t, _ in counted)
    peak_hour = max(hours, key=hours.get)
    quiet_hour = min(range(10, 18), key=lambda h: hours.get(h, 0))
    per_date = Counter(t.date() for t, _ in counted)
    best_day, best_count = per_date.most_common(1)[0]
    weekend = sum(t.weekday() >= 5 for t, _ in counted) / len(counted)
    night = sum(t.hour >= 22 or t.hour < 6 for t, _ in counted)
    weekday = Counter(t.strftime("%a") for t, _ in counted).most_common(1)[0][0]

    added = removed = tests = biggest = 0
    langs = Counter()
    for c in data["commits"]:
        a_sum = 0
        for path, a, r in c["files"]:
            if EXCLUDED.search(path):
                continue
            a_sum += a
            removed += r
            langs[language(path)] += a + r
            tests += (a + r) if TESTS.search(path) else 0
        added += a_sum
        biggest = max(biggest, a_sum)

    since = data["events_since"]
    repos = ranked_repos(data, acts)
    quiet_note = "quiet hour. lunch is sacred" if 12 <= quiet_hour <= 14 else "quietest work hour"
    return {
        "name": data["name"],
        "host": data["host"],
        "terms": data["terms"],
        "now": now,
        "first": first,
        "chapter": (today - first).days + 1,
        "days": days,
        "daily": daily,
        "kinds": kinds,
        "opened": data["opened"],
        "commits": len(data["commits"]),
        "events": data["events"],
        "repos": repos,
        "touched": sum(size for _, _, size in repos),
        "scanned": len(data["repos"]),
        "active_window": sum(1 for d in days if daily[d]),
        "longest": longest,
        "current": current,
        "weekday": weekday,
        "events_since": since.astimezone(TZ).date() if since else None,
        "added": added,
        "removed": removed,
        "tests": tests / max(1, added + removed),
        "biggest": biggest,
        "langs": langs,
        "facts": [
            (f"{longest} days", "longest streak"),
            (f"{peak_hour:02d}:00", "peak hour"),
            (f"{quiet_hour:02d}:00", quiet_note),
            (best_day.strftime("%d %b"), f"busiest day · {best_count} actions"),
            (f"{weekend:.0%}", "of it on weekends"),
            (f"{night}", "actions after 22:00"),
        ],
    }


def fonts(*names):
    files = {"Bangers": "bangers.woff2", "Dela": "dela.woff2"}
    return "".join(
        f"@font-face{{font-family:{n};src:url(data:font/woff2;base64,"
        f'{base64.b64encode((HERE / "fonts" / files[n]).read_bytes()).decode()}) format("woff2")}}'
        for n in names
    )


CSS = """
.t{font-family:Bangers,Impact,'Arial Black',sans-serif;letter-spacing:.03em}
.j{font-family:Dela,'Hiragino Sans','Yu Gothic','Noto Sans JP',sans-serif;font-weight:900}
.s{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
.m{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace}
.b{font-weight:700}
.halo{paint-order:stroke;stroke:#fbf8f1;stroke-linejoin:round}
.kanji{stroke:#fbf8f1;stroke-width:1.1px;stroke-linejoin:round}
.kanji-dark{stroke:#141414;stroke-width:1.1px;stroke-linejoin:round}
.stamp-ink{stroke:#100e13;stroke-width:1.3px;stroke-linejoin:round}
.menace{paint-order:stroke;stroke:#141414;stroke-width:1.6px;stroke-linejoin:round}
.pop{transform-box:fill-box;transform-origin:center;animation:pop .6s cubic-bezier(.3,1.7,.5,1) backwards}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .7s cubic-bezier(.2,.8,.2,1) backwards}
.slide{transform-box:fill-box;transform-origin:0 50%;animation:slide .8s cubic-bezier(.2,.8,.2,1) backwards}
.float{animation:float 1.6s ease-in-out infinite}
@keyframes pop{from{transform:scale(0)}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes slide{from{transform:scaleX(0)}}
@keyframes float{50%{transform:translateY(-4px)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
"""


def svg(w, h, title, body, css="", faces=("Bangers", "Dela")):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f"<title>{escape(title)}</title><style>{fonts(*faces)}{CSS}{css}</style>{body}</svg>"
    )


def panel(w, h, fill=PAPER):
    return f'<rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" rx="3" fill="{fill}" stroke="{INK}" stroke-width="3"/>'


def text(x, y, s, cls="s", size=11, fill=INK, anchor="start", extra=""):
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" {extra}>{escape(str(s))}</text>'
    )


def parts(x, y, items, size=10.5, anchor="start"):
    spans = "".join(
        f'<tspan fill="{INK if bold else MUTE}" font-weight="{700 if bold else 400}">{escape(s)}</tspan>'
        for s, bold in items
    )
    return f'<text x="{x}" y="{y}" class="s" font-size="{size}" text-anchor="{anchor}">{spans}</text>'


def width(s, size, kind="s"):
    return len(s) * size * {"s": 0.53, "sb": 0.6, "t": 0.44, "j": 1.0, "m": 0.61}[kind]


def column(x, y, w, h, r):
    r = min(r, w / 2, h)
    return (
        f"M{x:.2f},{y + h:.2f}V{y + r:.2f}Q{x:.2f},{y:.2f} {x + r:.2f},{y:.2f}"
        f"H{x + w - r:.2f}Q{x + w:.2f},{y:.2f} {x + w:.2f},{y + r:.2f}V{y + h:.2f}Z"
    )


def bar(x, y, w, h, r):
    r = min(r, h / 2, w)
    return (
        f"M{x:.2f},{y:.2f}H{x + w - r:.2f}Q{x + w:.2f},{y:.2f} {x + w:.2f},{y + r:.2f}"
        f"V{y + h - r:.2f}Q{x + w:.2f},{y + h:.2f} {x + w - r:.2f},{y + h:.2f}H{x:.2f}Z"
    )


def star(cx, cy, outer, inner, n=5, jitter=0.0, seed=7):
    rnd = random.Random(seed)
    pts = []
    for i in range(n * 2):
        r = (outer if i % 2 == 0 else inner) * (1 + rnd.uniform(-jitter, jitter))
        a = math.radians(-90 + i * 180 / n)
        pts.append(f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}")
    return "M" + "L".join(pts) + "Z"


def halftone(key, cx, cy, r, strength=0.5):
    return (
        f'<pattern id="{key}p" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<circle cx="3" cy="3" r="1.5" fill="{INK}"/></pattern>'
        f'<radialGradient id="{key}g" cx="{cx}" cy="{cy}" r="{r}" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="#fff" stop-opacity="{strength}"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
        f'<mask id="{key}m"><rect width="100%" height="100%" fill="url(#{key}g)"/></mask>'
    )


def legend(x_right, y, items, size=10):
    out, x = [], x_right
    for label, color, shape in reversed(items):
        x -= width(label, size)
        out.append(text(x, y, label, size=size, fill=MUTE))
        x -= 14
        if shape == "star":
            out.append(f'<path d="{star(x + 5, y - 3.5, 5, 2.2)}" fill="{INK}"/>')
        else:
            out.append(f'<rect x="{x}" y="{y - 8.5}" width="10" height="10" rx="2" fill="{color}"/>')
        x -= 14
    return "".join(out)


def compact(n):
    return f"{n / 1000:.1f}K" if n >= 10000 else f"{n:,}"


def hero(s):
    w, h = 440, 198
    rnd = random.Random(3)
    fx, fy = 330, 80
    lines = []
    for phase in range(2):
        seg = []
        for i in range(64):
            a = 2 * math.pi * (i + rnd.uniform(0.1, 0.9) + phase * 0.5) / 64
            r0 = rnd.uniform(96, 130)
            seg.append(
                f'<line x1="{fx + r0 * math.cos(a):.1f}" y1="{fy + r0 * 0.42 * math.sin(a):.1f}" '
                f'x2="{fx + 520 * math.cos(a):.1f}" y2="{fy + 520 * math.sin(a):.1f}" '
                f'stroke-width="{rnd.uniform(0.5, 1.8):.2f}"/>'
            )
        lines.append(f'<g class="sl sl{phase}">{"".join(seg)}</g>')

    bubble_rx, bubble_ry = 92, 33
    facts = []
    for i, (big, small) in enumerate(s["facts"]):
        facts.append(
            f'<g class="f f{i}" style="animation-delay:{i * 3}s">'
            f'{text(fx, fy + 2, big.upper(), "t", 24, anchor="middle")}'
            f'{text(fx, fy + 17, small, "s b", 9.5, MUTE, anchor="middle")}</g>'
        )

    menace = "".join(
        f'<g class="float" style="animation-delay:{i * 0.25}s">'
        f'<g transform="rotate(-12 {262 + i * 34} {38 + (i % 2) * 6})">{text(262 + i * 34, 38 + (i % 2) * 6, JP["menace"], "j menace", 27, RED)}</g></g>'
        for i in range(3)
    )

    chapter = f'{JP["chapter"][0]}{s["chapter"]}{JP["chapter"][1]}'
    tag_w = width(chapter, 17, "j") * 0.8 + 18
    cells = [
        (f'{s["commits"]:,}', "commits"),
        (f'{s["opened"]:,}', f'{s["terms"][1]} opened'),
        (f'{s["kinds"]["review"]:,}', "reviews"),
        (f'+{compact(s["added"])}', "lines added"),
    ]
    cell_w = (w - 3) / 4
    stats = "".join(
        f'<g class="pop" style="animation-delay:{0.3 + i * 0.12:.2f}s">'
        f'{text(1.5 + cell_w * (i + 0.5), 168, value, "t", 27, anchor="middle")}'
        f'{text(1.5 + cell_w * (i + 0.5), 184, label.upper(), "s b", 8, MUTE, anchor="middle", extra=SPACED)}</g>'
        for i, (value, label) in enumerate(cells)
    )
    dividers = "".join(f'<line x1="{1.5 + cell_w * i:.1f}" y1="132" x2="{1.5 + cell_w * i:.1f}" y2="196" stroke="{INK}" stroke-width="2"/>' for i in (1, 2, 3))

    body = (
        f"<defs>{halftone('ht', 60, 190, 120, 0.35)}<clipPath id=\"top\"><rect x=\"3\" y=\"3\" width=\"{w - 6}\" height=\"128\"/></clipPath></defs>"
        f"{panel(w, h)}"
        f'<g clip-path="url(#top)" stroke="{INK}" stroke-opacity=".13">{"".join(lines)}</g>'
        f'<rect x="3" y="132" width="{w - 6}" height="{h - 135}" fill="url(#htp)" mask="url(#htm)"/>'
        f'<rect x="12" y="11" width="{tag_w:.1f}" height="28" rx="2" fill="{INK}"/>'
        f'{text(21, 31.5, chapter, "j kanji-dark", 17, PAPER)}'
        f'{text(12 + tag_w + 8, 30, "since " + s["first"].strftime("%d %b %Y").lower(), "s b", 9.5, MUTE)}'
        f"{menace}"
        f'{text(14, 79, s["name"].split()[0].upper(), "t halo", 38, INK, extra=HALO6)}'
        f'{text(14, 112, " ".join(s["name"].split()[1:]).upper(), "t halo", 38, RED, extra=HALO6)}'
        f'{text(15, 125, ROLE, "s b halo", 9.5, MUTE, extra=HALO4) if ROLE else ""}'
        f'<ellipse cx="{fx}" cy="{fy}" rx="{bubble_rx}" ry="{bubble_ry}" fill="{PAPER}" stroke="{INK}" stroke-width="2"/>'
        f'<path d="M{fx + 78},{fy - 10} L{w - 5},{fy + 18} L{fx + 80},{fy + 8}" fill="{PAPER}" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>'
        f'{"".join(facts)}'
        f'<line x1="1.5" y1="132" x2="{w - 1.5}" y2="132" stroke="{INK}" stroke-width="2"/>'
        f"{dividers}{stats}"
    )
    n = len(facts)
    css = (
        ".sl{animation:shake .5s steps(1) infinite}.sl1{animation-delay:.25s;opacity:0}"
        "@keyframes shake{0%{opacity:1}50%{opacity:0}}"
        f".f{{opacity:0;animation:cycle {n * 3}s infinite backwards}}.f0{{opacity:1}}"
        f"@keyframes cycle{{0%{{opacity:0;transform:translateY(4px)}}{100 / n * 0.12:.1f}%,{100 / n * 0.9:.1f}%{{opacity:1;transform:none}}{100 / n:.1f}%,100%{{opacity:0}}}}"
    )
    return svg(w, h, f"{s['name']}, chapter {s['chapter']}", body, css)


def daily(s):
    w, h = 800, 250
    days, data = s["days"], s["daily"]
    x0, x1, y_top, y_base = 46, 784, 100, 200
    totals = [sum(data[d][k] for k, _ in SERIES) for d in days]
    peak = max(totals) or 1
    step = next(st for st in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500) if peak / st <= 4)
    y_max = math.ceil(peak / step) * step
    scale = (y_base - y_top) / y_max
    slot = (x1 - x0) / len(days)
    bw = max(1.2, min(12, slot * 0.74))

    grid = []
    for v in range(step, y_max + 1, step):
        y = y_base - v * scale
        grid.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="{RULE}" stroke-width="1"/>')
        grid.append(text(x0 - 8, y + 3.5, v, size=9.5, fill=MUTE, anchor="end"))
    grid.append(text(x0 - 8, y_base + 3.5, 0, size=9.5, fill=MUTE, anchor="end"))

    bars, stars, months = [], [], []
    for i, d in enumerate(days):
        x = x0 + i * slot + (slot - bw) / 2
        acc, segs = 0.0, []
        values = [(data[d][k], color) for k, color in SERIES if data[d][k]]
        for j, (v, color) in enumerate(values):
            hgt = v * scale - (1 if j < len(values) - 1 else 0)
            y = y_base - acc - hgt
            radius = min(1.6, bw / 2) if j == len(values) - 1 else 0
            segs.append(f'<path d="{column(x, y, bw, max(hgt, 0.8), radius)}" fill="{color}"/>')
            acc += hgt + 1
        if segs:
            bars.append(f'<g class="grow" style="animation-delay:{i * 5}ms">{"".join(segs)}</g>')
        if data[d]["release"]:
            stars.append(f'<path class="pop" style="animation-delay:{0.9 + i * 0.004:.2f}s" d="{star(x + bw / 2, y_base - acc - 6, 4.2, 1.9)}" fill="{INK}"/>')
        if d.day == 1 or (i == 0 and d.day < 22):
            months.append(text(x + bw / 2, y_base + 15, d.strftime("%b").upper(), "s b", 9, MUTE, anchor="start" if i == 0 else "middle"))

    p = max(range(len(days)), key=lambda i: (totals[i], i))
    px = x0 + p * slot + slot / 2
    burst_x = min(max(px, x0 + 28), x1 - 26)
    label = f"{totals[p]} on {days[p].strftime('%d %b')}"
    lx, anchor = (burst_x + 32, "start") if burst_x + 32 + width(label, 11, "sb") < x1 else (burst_x - 32, "end")
    burst = (
        f'<g class="pop" style="animation-delay:1.1s">'
        f'<path d="{star(burst_x, 84, 23, 15, 13, 0.2, 5)}" fill="{RED}" stroke="{INK}" stroke-width="1.6" stroke-linejoin="round"/>'
        f'{text(burst_x, 88.5, JP["boom"], "j", 11, PAPER, anchor="middle")}'
        f'{text(lx, 88.5, label, "s b", 11, INK, anchor=anchor)}</g>'
    )

    items = [("commits", INK, "box"), (s["terms"][0], RED, "box"), ("reviews & comments", TONE, "box")]
    items += [("private & other", LIGHT, "box")] if s["kinds"]["private"] else []
    items += [("release", INK, "star")] if s["kinds"]["release"] else []
    note = ""
    if s["events_since"] and s["events_since"] > days[0]:
        note = text(x1, 238, f"{s['terms'][1]} & reviews counted since {s['events_since'].strftime('%d %b')}", size=9.5, fill=MUTE, anchor="end")
    body = (
        f"{panel(w, h)}"
        f'{text(20, 42, JP["daily"], "j kanji", 26)}'
        f'{text(20 + width(JP["daily"], 26, "j") + 12, 42, "DAILY LOG", "t", 28)}'
        f'{text(20, 56, "contributions per day, " + days[0].strftime("%d %b") + " to " + days[-1].strftime("%d %b"), size=10.5, fill=MUTE)}'
        f"{legend(x1, 38, items)}"
        f'{"".join(grid)}'
        f'<line x1="{x0}" y1="{y_base}" x2="{x1}" y2="{y_base}" stroke="{INK}" stroke-width="1.2"/>'
        f'{"".join(bars)}{"".join(stars)}{"".join(months)}{burst}'
        + parts(20, 238, [
            ("active ", False), (f"{s['active_window']}", True), (f" of {len(days)} days   ·   longest streak ", False),
            (f"{s['longest']}", True), ("   ·   current ", False), (f"{s['current']}", True),
            ("   ·   favourite day ", False), (s["weekday"].lower(), True),
        ])
        + note
    )
    return svg(w, h, "Daily contributions", body)


def spider(cx, cy):
    legs = []
    for side in (-1, 1):
        for k, (dy, reach) in enumerate(((-6, 10), (-2, 12), (2, 12), (6, 10))):
            knee_x, knee_y = cx + side * reach * 0.6, cy + dy - 4 + k
            legs.append(f'<path d="M{cx},{cy} Q{knee_x},{knee_y - 5} {cx + side * reach},{cy + dy + 3}" fill="none"/>')
    return (
        f'<g stroke="{INK}" stroke-width="1.4" stroke-linecap="round">{"".join(legs)}</g>'
        f'<ellipse cx="{cx}" cy="{cy + 2}" rx="4.2" ry="5.2" fill="{INK}"/><circle cx="{cx}" cy="{cy - 4.5}" r="2.8" fill="{INK}"/>'
    )


def repos(s):
    w, h = 440, 200
    ranked = s["repos"]
    rows = ranked[:6]
    peak = max(sum(c.values()) for _, c, _ in rows)
    bx0, bx1 = 178, 392
    out = []
    for i, (name, counts, _) in enumerate(rows):
        cy = 72 + i * 19
        name = name if len(name) <= 20 else name[:19] + "…"
        x, segs = bx0, []
        values = [(counts[k], color) for k, color in SERIES if counts[k]]
        for j, (v, color) in enumerate(values):
            seg_w = v / peak * (bx1 - bx0)
            last = j == len(values) - 1
            segs.append(f'<path d="{bar(x, cy - 5, max(seg_w - (0 if last else 1.5), 0.8), 10, 3 if last else 0)}" fill="{color}"/>')
            x += seg_w
        out.append(
            f"{text(16, cy + 4.5, f'No.{i + 1}', 't', 13)}"
            f"{text(52, cy + 4, name, 's b', 10.5)}"
            f'<g class="slide" style="animation-delay:{0.15 + i * 0.09:.2f}s">{"".join(segs)}</g>'
            f"{text(x + 5, cy + 4.5, sum(counts.values()), 't', 13)}"
        )
    rest = s["touched"] - sum(size for _, _, size in rows)
    total = sum(sum(c.values()) for _, c, _ in ranked)
    items = [("commits", INK, "box"), (s["terms"][1], RED, "box"), ("reviews", TONE, "box")]
    body = (
        f"<defs>{halftone('ht', 440, 200, 190, 0.4)}</defs>"
        f"{panel(w, h)}"
        f'<rect x="3" y="3" width="{w - 6}" height="{h - 6}" fill="url(#htp)" mask="url(#htm)"/>'
        f"{spider(26, 26)}"
        f'{text(46, 37, JP["troupe"], "j kanji", 24)}'
        f'{text(46 + width(JP["troupe"], 24, "j") + 10, 37, "PHANTOM TROUPE", "t", 23)}'
        f'{text(16, 52, "contributions per repo, all time", size=9.5, fill=MUTE)}'
        f"{legend(w - 16, 52, items, 9.5)}"
        f'<line x1="14" y1="59" x2="{w - 14}" y2="59" stroke="{INK}" stroke-width="1.5"/>'
        f'{"".join(out)}'
        + parts(16, 190, [
            (f"+{rest} more" if rest else "that's all of them", True),
            (f"   ·   {s['touched']} repos touched   ·   ", False), (f"{total:,}", True), (" contributions", False),
        ], 9.5)
    )
    return svg(w, h, "Contributions per repo", body)


def change_breakdown(s, w):
    commits = max(1, s["commits"])
    langs = [(k, v) for k, v in s["langs"].most_common() if k != "Other"][:4]
    lang_total = max(1, sum(s["langs"].values()))
    top = langs[0][1] if langs else 1
    lang_rows = "".join(
        f"{text(16, 150 + i * 13, name.upper(), 's b', 8.5)}"
        f'<path class="slide" style="animation-delay:{0.9 + i * 0.1:.2f}s" d="{bar(112, 144 + i * 13, max(1.5, 68 * v / top), 7, 2)}" fill="{INK}"/>'
        f"{text(212, 150 + i * 13, f'{v / lang_total:.0%}', 's b', 9, anchor='end')}"
        for i, (name, v) in enumerate(langs)
    )
    numbers = [
        ("net", f"{s['added'] - s['removed']:+,}".replace("-", "−")),
        ("in tests", f"{s['tests']:.0%}"),
        ("biggest commit", f"+{s['biggest']:,}"),
        ("per commit", f"+{s['added'] // commits} / −{s['removed'] // commits}"),
    ]
    number_rows = "".join(
        f"{text(236, 150 + i * 13, label.upper(), 's b', 8.5, MUTE)}"
        f"{text(w - 16, 151 + i * 13, value, 't', 14.5, anchor='end')}"
        for i, (label, value) in enumerate(numbers)
    )
    return (
        f'<line x1="1.5" y1="128" x2="{w - 1.5}" y2="128" stroke="{INK}" stroke-width="2"/>'
        f'<line x1="224" y1="128" x2="224" y2="196" stroke="{INK}" stroke-width="2"/>'
        f'{text(16, 138, "BY LANGUAGE", "s b", 7.5, MUTE, extra=SPACED)}'
        f'{text(236, 138, "BY THE NUMBERS", "s b", 7.5, MUTE, extra=SPACED)}'
        f"{lang_rows}{number_rows}"
    )


SUITS = {
    "heart": '<path d="M0,-2.2C-1.2,-4.6 -5,-4.4 -5,-1.2C-5,1.8 -1.6,3.6 0,5.4C1.6,3.6 5,1.8 5,-1.2C5,-4.4 1.2,-4.6 0,-2.2Z"/>',
    "diamond": '<path d="M0,-5.4L4,0L0,5.4L-4,0Z"/>',
    "spade": '<path d="M0,-5.4C-1.6,-3.4 -5,-1.6 -5,1.2C-5,3.8 -1.8,4.4 -0.6,2.6L-1.6,5.6H1.6L0.6,2.6C1.8,4.4 5,3.8 5,1.2C5,-1.6 1.6,-3.4 0,-5.4Z"/>',
    "club": '<circle cx="0" cy="-2.7" r="2.4"/><circle cx="-2.7" cy="1.2" r="2.4"/><circle cx="2.7" cy="1.2" r="2.4"/><path d="M-0.6,0.6L-1.6,5.6H1.6L0.6,0.6Z"/>',
}


def suit(kind, x, y, scale, color, flip=False):
    turn = " rotate(180)" if flip else ""
    return f'<g transform="translate({x:.1f} {y:.1f}) scale({scale}){turn}" fill="{color}">{SUITS[kind]}</g>'


def hand(x, y, value, red, start_delay):
    cw, ch, pitch = 29, 44, 27
    sign, digits = value[0], value[1:]
    color = RED if red else INK
    kinds = ("heart", "diamond") if red else ("spade", "club")
    out = [text(x + 6, y + 31, sign, "t", 30, INK, anchor="middle")]
    cx = x + 16
    for i, char in enumerate(digits):
        if i and (len(digits) - i) % 3 == 0:
            cx += 7
        kind = kinds[i % 2]
        tilt = (i - (len(digits) - 1) / 2) * 2.2
        out.append(
            f'<g class="deal" style="animation-delay:{start_delay + i * 0.11:.2f}s">'
            f'<g transform="rotate({tilt:.1f} {cx + cw / 2:.1f} {y + ch:.1f})">'
            f'<rect x="{cx}" y="{y}" width="{cw}" height="{ch}" rx="3.5" fill="{PAPER}" stroke="{INK}" stroke-width="1.5"/>'
            f'{text(cx + 4, y + 10, char, "t", 9, color)}'
            f"{suit(kind, cx + 6.5, y + 16, 0.55, color)}"
            f'{text(cx + cw / 2, y + 31, char, "t", 24, color, anchor="middle")}'
            f"{suit(kind, cx + cw - 6.5, y + ch - 7, 0.55, color, flip=True)}"
            f"</g></g>"
        )
        cx += pitch
    return "".join(out)


def changes(s):
    w, h = 440, 198
    body = (
        f"<defs>{halftone('ht', 440, 60, 150, 0.3)}</defs>"
        f"{panel(w, h)}"
        f'<rect x="3" y="3" width="{w - 6}" height="122" fill="url(#htp)" mask="url(#htm)"/>'
        f'{text(16, 35, JP["bungee"], "j kanji", 22)}'
        f'{text(16 + width(JP["bungee"], 22, "j") + 10, 35, "BUNGEE GUM", "t", 23)}'
        f'{text(16, 48, "lines stretched and shrunk · lockfiles, migrations and assets excluded", size=9, fill=MUTE)}'
        f'{text(16, 64, "STRETCHED", "s b", 7.5, MUTE, extra=SPACED)}'
        f'{text(238, 64, "SHRUNK", "s b", 7.5, MUTE, extra=SPACED)}'
        f'<path class="gum" d="M{190},92 C{204},78 {216},106 {230},92" fill="none" stroke="{RED}" stroke-width="2.2" stroke-linecap="round"/>'
        f'{hand(14, 70, "+" + str(s["added"]), True, 0.2)}'
        f'{hand(236, 70, "−" + str(s["removed"]), False, 0.9)}'
        f"{change_breakdown(s, w)}"
    )
    css = (
        ".deal{transform-box:fill-box;transform-origin:center;animation:deal .55s cubic-bezier(.2,.9,.3,1.2) backwards}"
        "@keyframes deal{from{transform:translate(-60px,-24px) rotate(-40deg);opacity:0}}"
        ".gum{transform-box:fill-box;transform-origin:0 50%;animation:gum 1.8s ease-in-out infinite}"
        "@keyframes gum{50%{transform:scaleX(1.18) scaleY(.6)}}"
    )
    return svg(w, h, "Lines of code stretched and shrunk", body, css)


def needle(x, y, length, angle, delay):
    return (
        f'<g class="needle" style="animation-delay:{delay:.2f}s"><g transform="translate({x} {y}) rotate({angle})">'
        f'<line x1="0" y1="0" x2="{length}" y2="0" stroke="#d9d4e0" stroke-width="1.6" stroke-linecap="round"/>'
        f'<circle cx="{length}" cy="0" r="3.6" fill="{PAPER}" stroke="{INK}" stroke-width="1"/></g></g>'
    )


def footer(s):
    w, h = 720, 160
    light, dim = "#ece8f0", "#8b8497"
    stamp = s["now"].strftime("%d %b %Y · %H:%M").lower()
    rows = [
        ("completed", f"{stamp} {TZ_NAME}"),
        ("client", f"{s['host']} api"),
        ("targets", f"{s['scanned']} repos · {s['commits']:,} commits · {s['events']:,} events"),
        ("next job", "tomorrow, 06:00"),
    ]
    report = "".join(
        f"{text(338, 76 + i * 19, label, 'm', 11.5, dim)}{text(424, 76 + i * 19, value, 'm', 11.5, light)}"
        for i, (label, value) in enumerate(rows)
    )
    needles = "".join(needle(x, y, length, angle, 0.5 + i * 0.18) for i, (x, y, length, angle) in enumerate(
        ((314, 52, 46, -148), (316, 90, 58, -136), (313, 126, 42, -158))
    ))
    body = (
        f'<defs><radialGradient id="vig" cx="30%" cy="40%" r="85%"><stop offset=".5" stop-color="#000" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#000" stop-opacity=".5"/></radialGradient></defs>'
        f'{panel(w, h, "#100e13")}'
        f'<rect x="3" y="3" width="{w - 6}" height="{h - 6}" fill="url(#vig)"/>'
        f'{text(24, 62, JP["job"], "j kanji-dark", 32, light)}'
        f'{text(24, 116, "JOB DONE", "t", 46, light)}'
        f'<g class="stamp"><g transform="rotate(-14 214 52)">'
        f'<circle cx="214" cy="52" r="25" fill="none" stroke="{RED}" stroke-width="2.6"/>'
        f'<circle cx="214" cy="52" r="20.5" fill="none" stroke="{RED}" stroke-width="1"/>'
        f'{text(214, 62, JP["stamp"], "j stamp-ink", 28, RED, anchor="middle")}</g></g>'
        f"{needles}"
        f'<line x1="316" y1="22" x2="316" y2="{h - 22}" stroke="#2c2733" stroke-width="1.5"/>'
        f'{text(338, 48, "ZOLDYCK FAMILY · JOB REPORT", "s b", 8.5, dim, extra=SPACED)}'
        f"{report}"
    )
    css = (
        ".needle{transform-box:fill-box;animation:throw .45s cubic-bezier(.2,.9,.3,1) backwards}"
        "@keyframes throw{from{transform:translate(-80px,-50px);opacity:0}}"
        ".stamp{transform-box:fill-box;transform-origin:center;animation:stamp .35s cubic-bezier(.3,1.6,.5,1) 1.3s backwards}"
        "@keyframes stamp{from{transform:scale(1.8);opacity:0}}"
    )
    return svg(w, h, "Job done: last sync", body, css)


def main(out):
    s = summarize(collect())
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, render in (("hero", hero), ("daily", daily), ("repos", repos), ("changes", changes), ("footer", footer)):
        (out / f"{name}.svg").write_text(render(s), encoding="utf-8")
    print(f"{s['commits']} commits, {s['events']} events, {len(s['repos'])} repos -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
