#!/usr/bin/env python3
"""Generate the SVG assets and dynamic README sections for this GitHub profile.

Standard library only. No third-party services are called at view time:
every image in the README is an SVG committed to this repository.

Usage:
    python scripts/generate.py            # fetch live data, fall back to snapshot
    python scripts/generate.py --offline  # use data/snapshot.json only

Environment:
    GITHUB_TOKEN   optional, raises the API rate limit (set automatically in Actions)
"""
from __future__ import annotations

import argparse
import colorsys
import datetime as dt
import html
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"

FONT_SANS = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
FONT_MONO = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace"

THEMES = {
    "dark": {
        "bg": "#0d1117", "card": "#151b23", "border": "#30363d",
        "fg": "#f0f6fc", "sub": "#9198a1", "acc": "#58a6ff",
        "heat": ["#1b222c", "#0c2d6b", "#1158c7", "#388bfd", "#79c0ff"],
    },
    "light": {
        "bg": "#f6f8fa", "card": "#ffffff", "border": "#d1d9e0",
        "fg": "#1f2328", "sub": "#59636e", "acc": "#0969da",
        "heat": ["#eaeef2", "#b6e3ff", "#54aeff", "#0969da", "#0550ae"],
    },
}

LANG_COLORS = {
    "JavaScript": "#f1e05a", "TypeScript": "#3178c6", "PHP": "#4F5D95",
    "Python": "#3572A5", "HTML": "#e34c26", "CSS": "#663399", "SCSS": "#c6538c",
    "Java": "#b07219", "C": "#7d7d7d", "C++": "#f34b7d", "C#": "#178600",
    "Go": "#00ADD8", "Rust": "#dea584", "Shell": "#89e051", "Kotlin": "#A97BFF",
    "Dart": "#00B4AB", "Vue": "#41b883", "Ruby": "#701516", "Swift": "#F05138",
    "Jupyter Notebook": "#DA5B0B", "Blade": "#f7523f", "Dockerfile": "#384d54",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def esc(s) -> str:
    return html.escape(str(s), quote=False)


def lang_color(name: str) -> str:
    if name in LANG_COLORS:
        return LANG_COLORS[name]
    hue = (sum(ord(c) * (i + 7) for i, c in enumerate(name)) % 360) / 360
    r, g, b = colorsys.hls_to_rgb(hue, 0.58, 0.55)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def rel_time(iso: str | None, now: dt.datetime) -> str | None:
    if not iso:
        return None
    delta = now - parse_iso(iso)
    days = delta.days
    if days < 1:
        return "today"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def text_w(s: str, size: float, mono: bool = False) -> float:
    return len(s) * size * (0.6 if mono else 0.56)


def wrap(text: str, width: int, lines: int) -> list[str]:
    out = textwrap.wrap(text, width=width)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(" .,;:") + "…"
    return out


def svg_open(w: int, h: int, title: str, extra_defs: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">\n'
        f"  <title>{esc(title)}</title>\n"
        f"  <defs>{extra_defs}</defs>\n"
    )


def card_frame(w: int, h: int, t: dict) -> str:
    return (
        f'  <rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" '
        f'fill="{t["card"]}" stroke="{t["border"]}"/>\n'
    )


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #

def api_get(path: str, token: str | None = None, params: dict | None = None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "profile-generator",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_live(cfg: dict, token: str | None) -> dict:
    user_name = cfg["username"]
    user = api_get(f"/users/{user_name}", token)

    repos, page = [], 1
    while True:
        batch = api_get(
            f"/users/{user_name}/repos", token,
            {"per_page": 100, "page": page, "type": "owner", "sort": "pushed"},
        )
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    exclude = {n.lower() for n in cfg.get("exclude_repos", [])}
    kept = []
    for r in repos:
        if r.get("fork") or r["name"].lower() in exclude:
            continue
        try:
            langs = api_get(f"/repos/{user_name}/{r['name']}/languages", token)
        except urllib.error.URLError:
            langs = {}
        kept.append({
            "name": r["name"],
            "description": r.get("description") or "",
            "language": r.get("language"),
            "stargazers_count": r.get("stargazers_count", 0),
            "forks_count": r.get("forks_count", 0),
            "pushed_at": r.get("pushed_at"),
            "html_url": r.get("html_url"),
            "archived": r.get("archived", False),
            "languages": langs,
        })

    events = []
    for page in (1, 2, 3):
        try:
            batch = api_get(f"/users/{user_name}/events/public", token,
                            {"per_page": 100, "page": page})
        except urllib.error.URLError:
            break
        if not batch:
            break
        for e in batch:
            events.append({"type": e["type"], "repo": e["repo"]["name"],
                           "created_at": e["created_at"]})

    return {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "synthetic": False,
        "user": {k: user.get(k) for k in
                 ("login", "public_repos", "followers", "following", "created_at")},
        "repos": kept,
        "events": events,
    }


def load_data(root: Path, cfg: dict, offline: bool) -> dict:
    snapshot_path = root / "data" / "snapshot.json"
    if not offline:
        try:
            data = fetch_live(cfg, os.environ.get("GITHUB_TOKEN"))
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("Fetched live data.")
            return data
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            print(f"Live fetch failed ({exc}); using snapshot.", file=sys.stderr)
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# derived values
# --------------------------------------------------------------------------- #

def language_totals(data: dict) -> tuple[list[tuple[str, int]], bool]:
    totals: Counter = Counter()
    for r in data["repos"]:
        for name, size in (r.get("languages") or {}).items():
            totals[name] += size
    if totals:
        return totals.most_common(), True
    primary: Counter = Counter()
    for r in data["repos"]:
        if r.get("language"):
            primary[r["language"]] += 1
    return primary.most_common(), False


def activity_summary(data: dict, now: dt.datetime) -> dict:
    window_start = (now - dt.timedelta(days=89)).date()
    per_day: Counter = Counter()
    per_repo: Counter = Counter()
    for e in data.get("events", []):
        d = parse_iso(e["created_at"]).date()
        if d >= window_start:
            per_day[d] += 1
            per_repo[e["repo"].split("/")[-1]] += 1

    longest = run = 0
    day = window_start
    while day <= now.date():
        run = run + 1 if per_day.get(day) else 0
        longest = max(longest, run)
        day += dt.timedelta(days=1)

    return {
        "per_day": per_day,
        "events": sum(per_day.values()),
        "active_days": len(per_day),
        "longest_streak": longest,
        "top_repo": per_repo.most_common(1)[0][0] if per_repo else None,
        "window_start": window_start,
    }


def heat_level(n: int) -> int:
    if n <= 0:
        return 0
    if n <= 2:
        return 1
    if n <= 5:
        return 2
    if n <= 9:
        return 3
    return 4


# --------------------------------------------------------------------------- #
# SVG builders
# --------------------------------------------------------------------------- #

def build_header(cfg: dict, t: dict) -> str:
    tile = ""
    centers = sorted([(40, -20), (0, 0), (80, 0), (40, 20), (0, 40), (80, 40), (40, 60)],
                     key=lambda c: c[1])
    for cx, cy in centers:
        for r in (40, 30, 20, 10):
            tile += (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{t["bg"]}" '
                     f'stroke="{t["acc"]}" stroke-opacity="0.28" stroke-width="1.2"/>')
    defs = (
        f'<pattern id="sg" width="80" height="40" patternUnits="userSpaceOnUse">{tile}</pattern>'
        '<linearGradient id="fade" x1="0" x2="1" y1="0" y2="0">'
        '<stop offset="0.42" stop-color="#fff" stop-opacity="0"/>'
        '<stop offset="0.85" stop-color="#fff" stop-opacity="1"/></linearGradient>'
        '<mask id="m"><rect width="1200" height="260" fill="url(#fade)"/></mask>'
        '<clipPath id="c"><rect width="1200" height="260" rx="14"/></clipPath>'
        "<style>"
        "@media (prefers-reduced-motion: no-preference){"
        ".drift{animation:drift 70s linear infinite}"
        ".bar{transform-box:fill-box;transform-origin:left center;animation:grow 1.1s ease-out both}"
        ".r1{animation:rise .9s ease-out both}"
        ".r2{animation:rise .9s .15s ease-out both}"
        ".r3{animation:rise .9s .3s ease-out both}}"
        "@keyframes drift{from{transform:translateX(0)}to{transform:translateX(-80px)}}"
        "@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}"
        "@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}"
        "</style>"
    )
    title = f'{cfg["display_name"]}, {cfg["role"]}'
    out = svg_open(1200, 260, title, defs)
    out += '  <g clip-path="url(#c)">\n'
    out += f'    <rect width="1200" height="260" fill="{t["bg"]}"/>\n'
    out += '    <g mask="url(#m)"><g class="drift"><rect width="1280" height="260" fill="url(#sg)"/></g></g>\n'
    out += f'    <rect class="bar" x="72" y="62" width="44" height="4" rx="2" fill="{t["acc"]}"/>\n'
    out += (f'    <text class="r1" x="70" y="136" font-family="{FONT_SANS}" font-size="64" '
            f'font-weight="700" letter-spacing="-1.5" fill="{t["fg"]}">{esc(cfg["display_name"])}</text>\n')
    out += (f'    <text class="r2" x="72" y="178" font-family="{FONT_SANS}" font-size="24" '
            f'font-weight="500" fill="{t["acc"]}">{esc(cfg["role"])}</text>\n')
    out += (f'    <text class="r3" x="72" y="214" font-family="{FONT_SANS}" font-size="18" '
            f'fill="{t["sub"]}">{esc(cfg["tagline"])}</text>\n')
    out += "  </g>\n"
    out += (f'  <rect x="0.5" y="0.5" width="1199" height="259" rx="13.5" fill="none" '
            f'stroke="{t["border"]}"/>\n')
    return out + "</svg>\n"


def build_divider(t: dict) -> str:
    defs = (f'<linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="{t["border"]}" stop-opacity="0"/>'
            f'<stop offset="0.5" stop-color="{t["border"]}"/>'
            f'<stop offset="1" stop-color="{t["border"]}" stop-opacity="0"/></linearGradient>')
    out = svg_open(1200, 24, "Section divider", defs)
    out += '  <rect x="0" y="11.5" width="1200" height="1" fill="url(#g)"/>\n'
    out += f'  <rect x="596" y="8" width="8" height="8" transform="rotate(45 600 12)" fill="{t["acc"]}"/>\n'
    return out + "</svg>\n"


def build_stats(data: dict, now: dt.datetime, t: dict) -> str:
    user = data["user"]
    langs, _ = language_totals(data)
    stars = [r.get("stargazers_count") for r in data["repos"]]
    last_push = max((r["pushed_at"] for r in data["repos"] if r.get("pushed_at")), default=None)

    items = [
        (user.get("public_repos"), "Public repositories"),
        (sum(stars) if stars and all(s is not None for s in stars) else None, "Stars received"),
        (user.get("followers"), "Followers"),
        (len(langs) or None, "Languages"),
        (rel_time(last_push, now), "Last push"),
    ]
    items = [(v, l) for v, l in items if v is not None]
    w, h = 840, 104
    out = svg_open(w, h, "Profile at a glance") + card_frame(w, h, t)
    pitch = (w - 48) / max(len(items), 1)
    for i, (value, label) in enumerate(items):
        cx = 24 + pitch * i + pitch / 2
        if i:
            x = 24 + pitch * i
            out += f'  <rect x="{x:.1f}" y="26" width="1" height="52" fill="{t["border"]}"/>\n'
        out += (f'  <text x="{cx:.1f}" y="55" text-anchor="middle" font-family="{FONT_SANS}" '
                f'font-size="30" font-weight="700" fill="{t["fg"]}">{esc(value)}</text>\n')
        out += (f'  <text x="{cx:.1f}" y="78" text-anchor="middle" font-family="{FONT_SANS}" '
                f'font-size="11" letter-spacing="0.8" fill="{t["sub"]}">{esc(label.upper())}</text>\n')
    return out + "</svg>\n"


def build_languages(data: dict, t: dict) -> str:
    langs, has_bytes = language_totals(data)
    top, rest = langs[:8], langs[8:]
    if rest and has_bytes:
        top.append(("Other", sum(v for _, v in rest)))
    total = sum(v for _, v in top) or 1

    cols, pitch = 4, 198
    rows = max((len(top) + cols - 1) // cols, 1)
    w = 840
    h = (98 if has_bytes else 62) + rows * 28 + 8
    out = svg_open(w, h, "Languages by code volume",
                   f'<clipPath id="bar"><rect x="24" y="52" width="{w - 48}" height="12" rx="6"/></clipPath>')
    out += card_frame(w, h, t)
    out += (f'  <text x="24" y="34" font-family="{FONT_SANS}" font-size="16" font-weight="600" '
            f'fill="{t["fg"]}">Languages</text>\n')
    sub = (f"by code volume across {len(data['repos'])} repositories" if has_bytes
           else "primary language per repository")
    out += (f'  <text x="{w - 24}" y="34" text-anchor="end" font-family="{FONT_SANS}" font-size="12" '
            f'fill="{t["sub"]}">{esc(sub)}</text>\n')

    y0 = 98 if has_bytes else 62
    if has_bytes:
        out += '  <g clip-path="url(#bar)">\n'
        x = 24.0
        for name, size in top:
            seg = (w - 48) * size / total
            color = "#8b949e" if name == "Other" else lang_color(name)
            out += f'    <rect x="{x:.2f}" y="52" width="{max(seg, 2):.2f}" height="12" fill="{color}"/>\n'
            x += seg
        out += "  </g>\n"

    for i, (name, size) in enumerate(top):
        cx = 24 + (i % cols) * pitch
        cy = y0 + (i // cols) * 28
        color = "#8b949e" if name == "Other" else lang_color(name)
        out += f'  <circle cx="{cx + 5}" cy="{cy - 4}" r="5" fill="{color}"/>\n'
        out += (f'  <text x="{cx + 18}" y="{cy}" font-family="{FONT_SANS}" font-size="13" '
                f'fill="{t["fg"]}">{esc(name)}</text>\n')
        if has_bytes:
            out += (f'  <text x="{cx + pitch - 22}" y="{cy}" text-anchor="end" font-family="{FONT_MONO}" '
                    f'font-size="12" fill="{t["sub"]}">{100 * size / total:.1f}%</text>\n')
    return out + "</svg>\n"


def _star(x: float, y: float, color: str) -> str:
    pts = [(6, 0), (7.8, 4.2), (12, 4.6), (8.9, 7.6), (9.8, 12), (6, 9.6), (2.2, 12),
           (3.1, 7.6), (0, 4.6), (4.2, 4.2)]
    p = " ".join(f"{x + a:.1f},{y + b:.1f}" for a, b in pts)
    return f'<polygon points="{p}" fill="none" stroke="{color}" stroke-width="1.2" stroke-linejoin="round"/>'


def _fork(x: float, y: float, color: str) -> str:
    return (f'<g fill="none" stroke="{color}" stroke-width="1.2">'
            f'<circle cx="{x + 2.5}" cy="{y + 2.5}" r="2"/><circle cx="{x + 9.5}" cy="{y + 2.5}" r="2"/>'
            f'<circle cx="{x + 6}" cy="{y + 10}" r="2"/>'
            f'<path d="M{x + 2.5} {y + 4.5} V{y + 6.5} Q{x + 2.5} {y + 7.5} {x + 3.5} {y + 7.5} H{x + 8.5} '
            f'Q{x + 9.5} {y + 7.5} {x + 9.5} {y + 6.5} V{y + 4.5} M{x + 6} {y + 7.5} V{y + 8}"/></g>')


def build_repo_card(repo: dict, description: str, now: dt.datetime, t: dict) -> str:
    w, h = 400, 136
    out = svg_open(w, h, f"Repository {repo['name']}") + card_frame(w, h, t)
    out += (f'  <text x="22" y="36" font-family="{FONT_SANS}" font-size="17" font-weight="600" '
            f'fill="{t["acc"]}">{esc(repo["name"])}</text>\n')

    lines = wrap(description, 52, 2) if description else []
    if lines:
        for i, line in enumerate(lines):
            out += (f'  <text x="22" y="{62 + i * 20}" font-family="{FONT_SANS}" font-size="13" '
                    f'fill="{t["sub"]}">{esc(line)}</text>\n')
    else:
        out += (f'  <text x="22" y="62" font-family="{FONT_SANS}" font-size="13" font-style="italic" '
                f'fill="{t["sub"]}" opacity="0.7">No description provided</text>\n')

    x, y = 22, 112
    lang = repo.get("language")
    if lang:
        out += f'  <circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{lang_color(lang)}"/>\n'
        out += (f'  <text x="{x + 18}" y="{y}" font-family="{FONT_SANS}" font-size="12" '
                f'fill="{t["sub"]}">{esc(lang)}</text>\n')
        x += 18 + text_w(lang, 12) + 18
    if repo.get("stargazers_count") is not None:
        out += "  " + _star(x, y - 11, t["sub"]) + "\n"
        out += (f'  <text x="{x + 18}" y="{y}" font-family="{FONT_SANS}" font-size="12" '
                f'fill="{t["sub"]}">{repo["stargazers_count"]}</text>\n')
        x += 18 + text_w(str(repo["stargazers_count"]), 12) + 14
    if repo.get("forks_count") is not None:
        out += "  " + _fork(x, y - 12, t["sub"]) + "\n"
        out += (f'  <text x="{x + 18}" y="{y}" font-family="{FONT_SANS}" font-size="12" '
                f'fill="{t["sub"]}">{repo["forks_count"]}</text>\n')
    updated = rel_time(repo.get("pushed_at"), now)
    if updated:
        out += (f'  <text x="{w - 22}" y="{y}" text-anchor="end" font-family="{FONT_SANS}" '
                f'font-size="12" fill="{t["sub"]}">Updated {esc(updated)}</text>\n')
    return out + "</svg>\n"


def build_activity(data: dict, now: dt.datetime, t: dict) -> str:
    summary = activity_summary(data, now)
    has_events = bool(data.get("events"))
    w, h = 840, 252
    out = svg_open(w, h, "Public activity over the last 90 days") + card_frame(w, h, t)
    out += (f'  <text x="24" y="34" font-family="{FONT_SANS}" font-size="16" font-weight="600" '
            f'fill="{t["fg"]}">Public activity</text>\n')
    out += (f'  <text x="342" y="34" text-anchor="end" font-family="{FONT_SANS}" font-size="12" '
            f'fill="{t["sub"]}">last 90 days</text>\n')

    cell, gap, gx, gy = 18, 4, 44, 62
    today = now.date()
    week_start = today - dt.timedelta(days=(today.weekday() + 1) % 7)
    first = week_start - dt.timedelta(weeks=12)

    last_month = None
    for wk in range(13):
        col_date = first + dt.timedelta(weeks=wk)
        if col_date.month != last_month:
            out += (f'  <text x="{gx + wk * (cell + gap)}" y="54" font-family="{FONT_SANS}" font-size="11" '
                    f'fill="{t["sub"]}">{col_date.strftime("%b")}</text>\n')
            last_month = col_date.month
        for d in range(7):
            day = col_date + dt.timedelta(days=d)
            if day > today:
                continue
            in_window = day >= summary["window_start"]
            level = heat_level(summary["per_day"].get(day, 0)) if in_window else 0
            op = "1" if in_window else "0.45"
            out += (f'  <rect x="{gx + wk * (cell + gap)}" y="{gy + d * (cell + gap)}" width="{cell}" '
                    f'height="{cell}" rx="4" fill="{t["heat"][level]}" opacity="{op}"/>\n')
    for d, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out += (f'  <text x="{gx - 8}" y="{gy + d * (cell + gap) + 13}" text-anchor="end" '
                f'font-family="{FONT_SANS}" font-size="10" fill="{t["sub"]}">{label}</text>\n')

    ly = gy + 7 * (cell + gap) + 14
    out += (f'  <text x="{gx}" y="{ly + 9}" font-family="{FONT_SANS}" font-size="11" '
            f'fill="{t["sub"]}">Less</text>\n')
    for i in range(5):
        out += (f'  <rect x="{gx + 32 + i * 16}" y="{ly}" width="12" height="12" rx="3" '
                f'fill="{t["heat"][i]}"/>\n')
    out += (f'  <text x="{gx + 32 + 5 * 16 + 4}" y="{ly + 9}" font-family="{FONT_SANS}" font-size="11" '
            f'fill="{t["sub"]}">More</text>\n')

    out += f'  <rect x="380" y="26" width="1" height="{h - 52}" fill="{t["border"]}"/>\n'
    metrics = [
        (summary["events"], "Public events"),
        (summary["active_days"], "Active days"),
        (summary["longest_streak"], "Longest streak (days)"),
        (summary["top_repo"] or "n/a", "Most active repository"),
    ]
    for i, (value, label) in enumerate(metrics):
        x = 412 + (i % 2) * 220
        y = 84 + (i // 2) * 80
        shown = str(value) if has_events or isinstance(value, str) else "-"
        size = 30 if len(shown) <= 6 else 20
        out += (f'  <text x="{x}" y="{y}" font-family="{FONT_SANS}" font-size="{size}" font-weight="700" '
                f'fill="{t["fg"]}">{esc(shown[:18])}</text>\n')
        out += (f'  <text x="{x}" y="{y + 22}" font-family="{FONT_SANS}" font-size="11" letter-spacing="0.6" '
                f'fill="{t["sub"]}">{esc(label.upper())}</text>\n')
    if not has_events:
        out += (f'  <text x="412" y="{h - 26}" font-family="{FONT_SANS}" font-size="12" '
                f'fill="{t["sub"]}">Fills in automatically after the first scheduled run.</text>\n')
    return out + "</svg>\n"


# --------------------------------------------------------------------------- #
# README sections
# --------------------------------------------------------------------------- #

def picture(name: str, alt: str, width: str = "100%") -> str:
    return (
        "<picture>"
        f'<source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg">'
        f'<source media="(prefers-color-scheme: light)" srcset="assets/{name}-light.svg">'
        f'<img alt="{esc(alt)}" src="assets/{name}-light.svg" width="{width}">'
        "</picture>"
    )


def projects_block(cfg: dict, data: dict) -> str:
    by_name = {r["name"]: r for r in data["repos"]}
    cells = []
    for p in cfg["pinned"]:
        repo = by_name.get(p["repo"], {"name": p["repo"]})
        url = repo.get("html_url") or f"https://github.com/{cfg['username']}/{p['repo']}"
        cells.append(
            f'<td width="50%" valign="top"><a href="{url}">'
            f'{picture("card-" + p["repo"], p["repo"])}</a></td>'
        )
    rows = []
    for i in range(0, len(cells), 2):
        pair = cells[i:i + 2]
        if len(pair) == 1:
            pair.append('<td width="50%"></td>')
        rows.append("<tr>\n" + "\n".join(pair) + "\n</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


VERBS = {
    "PushEvent": "Pushed to", "CreateEvent": "Created", "WatchEvent": "Starred",
    "ForkEvent": "Forked", "PullRequestEvent": "Pull request in", "IssuesEvent": "Issue in",
    "IssueCommentEvent": "Commented in", "PublicEvent": "Open-sourced",
    "ReleaseEvent": "Released", "PullRequestReviewEvent": "Reviewed in",
}


def recent_block(data: dict, now: dt.datetime, limit: int = 6) -> str:
    items, seen = [], set()
    for e in data.get("events", []):
        if e["type"] == "DeleteEvent":
            continue
        key = (e["type"], e["repo"], e["created_at"][:10])
        if key in seen:
            continue
        seen.add(key)
        verb = VERBS.get(e["type"], "Activity in")
        when = rel_time(e["created_at"], now)
        items.append(f'- {verb} [{e["repo"].split("/")[-1]}](https://github.com/{e["repo"]}) '
                     f'<sub>· {when}</sub>')
        if len(items) == limit:
            break
    if not items:
        return "_Recent public activity appears here after the first scheduled run._"
    return "\n".join(items)


def replace_block(text: str, name: str, content: str) -> str:
    pattern = re.compile(rf"(<!--{name}:START-->)(.*?)(<!--{name}:END-->)", re.S)
    if not pattern.search(text):
        raise ValueError(f"README is missing the {name} markers")
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", text, count=1)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def build_all(root: Path, cfg: dict, data: dict) -> None:
    now = parse_iso(data["generated"])
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    by_name = {r["name"]: r for r in data["repos"]}

    for theme, t in THEMES.items():
        (assets / f"header-{theme}.svg").write_text(build_header(cfg, t), encoding="utf-8")
        (assets / f"divider-{theme}.svg").write_text(build_divider(t), encoding="utf-8")
        (assets / f"stats-{theme}.svg").write_text(build_stats(data, now, t), encoding="utf-8")
        (assets / f"languages-{theme}.svg").write_text(build_languages(data, t), encoding="utf-8")
        (assets / f"activity-{theme}.svg").write_text(build_activity(data, now, t), encoding="utf-8")
        for p in cfg["pinned"]:
            repo = by_name.get(p["repo"], {"name": p["repo"]})
            desc = p.get("description") or repo.get("description") or ""
            (assets / f"card-{p['repo']}-{theme}.svg").write_text(
                build_repo_card(repo, desc, now, t), encoding="utf-8")

    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    readme = replace_block(readme, "PROJECTS", projects_block(cfg, data))
    readme = replace_block(readme, "RECENT", recent_block(data, now))
    stamp = now.strftime("%Y-%m-%d")
    readme = replace_block(
        readme, "UPDATED",
        f'<sub>Images and activity are generated by <a href="scripts/generate.py">scripts/generate.py</a> '
        f"and refreshed daily by GitHub Actions. Last run: {stamp}.</sub>")
    readme_path.write_text(readme, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="use data/snapshot.json only")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)

    cfg = json.loads((args.root / "config.json").read_text(encoding="utf-8"))
    data = load_data(args.root, cfg, args.offline)
    build_all(args.root, cfg, data)
    print("Profile assets generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
