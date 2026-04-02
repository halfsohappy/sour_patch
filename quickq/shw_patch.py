#!/usr/bin/env python3
"""shw_patch.py — DMX patch and fixture routing report for ChamSys .shw files.

Purpose: extract *everything a DMX controller needs to know* from a show file.
Outputs three things:

  1. Fixture personalities — complete channel-by-channel attribute lists
  2. Universe maps — per-universe tables of fixture → address → footprint
  3. Flat channel reference — every single DMX channel in the rig: universe,
     address, attribute name, which fixture it belongs to

Use this when writing a lighting controller or DMX sequencer for a venue: load
a show file from their existing console and get the full rig in one place.

Usage:
    python quickq/shw_patch.py path/to/show.shw
    python quickq/shw_patch.py path/to/show.shw --out /path/to/patch.html

Requires: pip install markdown
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shw_common import parse_shw, render_html


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dmx_bar(occupied: set, universe_size: int = 512) -> str:
    """Return a 64-char ASCII occupancy bar for a universe.

    Each character represents 8 DMX addresses (512 / 64 = 8).
    '█' = at least one address occupied, '░' = empty.
    Address 1-based.
    """
    BAR_WIDTH = 64
    step = universe_size / BAR_WIDTH
    chars = []
    for i in range(BAR_WIDTH):
        lo = int(i * step) + 1
        hi = int((i + 1) * step)
        used = any(addr in occupied for addr in range(lo, hi + 1))
        chars.append('█' if used else '░')
    return ''.join(chars)


# ── Markdown generation ───────────────────────────────────────────────────────

def generate_markdown(data, fallback_name):
    # Derive show name from comment block
    show_name = fallback_name
    for c in data['comments']:
        if 'Show saved as' in c:
            parts = c.split("'")
            if len(parts) >= 2:
                show_name = os.path.splitext(os.path.basename(parts[1]))[0]
            break

    lines = []

    # ── Title ─────────────────────────────────────────────────────────────────
    lines += [
        f"# {show_name} — DMX Patch",
        "",
        "Fixture routing and channel reference extracted from a ChamSys QuickQ `.shw` file.",
        "Use this document when building a DMX controller or sequencer for this venue.",
        "",
    ]

    # ── Overview ──────────────────────────────────────────────────────────────
    lines += ["## Overview", ""]

    # Stats per universe
    by_universe: dict = {}
    for h in data['heads'].values():
        if h['personality_id'] == 0:
            continue
        by_universe.setdefault(h['universe'], []).append(h)

    total_dmx_chans = 0
    for heads in by_universe.values():
        for h in heads:
            p = data['personalities'].get(h['personality_id'])
            if p:
                total_dmx_chans += p['chan_count']

    lines += [
        "| | |",
        "|---|---|",
        f"| **Show file** | `{show_name}.shw` |",
        f"| **Fixture personalities** | {len(data['personalities'])} |",
        f"| **Patched heads** | {len(data['heads'])} |",
        f"| **DMX universes used** | {len(by_universe)} |",
        f"| **Total DMX channels occupied** | {total_dmx_chans} |",
        "",
    ]

    # ── Fixture Personalities ─────────────────────────────────────────────────
    lines += [
        "## Fixture Personalities",
        "",
        "Each fixture type patched in this show.  The channel list defines what"
        " each DMX address offset (relative to the fixture's start address) controls.",
        "",
    ]

    # Only personalities actually patched
    patched_pids = {h['personality_id'] for h in data['heads'].values()}

    for pid, p in sorted(data['personalities'].items()):
        if pid not in patched_pids:
            continue

        head_count = sum(1 for h in data['heads'].values() if h['personality_id'] == pid)
        chans = p['channels'][:p['chan_count']]

        lines += [
            f"### {p['model']}",
            "",
            f"**Manufacturer:** {p['manufacturer']} &nbsp;|&nbsp; "
            f"**Mode:** {p['mode']} &nbsp;|&nbsp; "
            f"**Footprint:** {p['chan_count']} ch &nbsp;|&nbsp; "
            f"**Patched:** {head_count} fixture{'s' if head_count != 1 else ''}",
            "",
        ]

        if chans:
            lines += ["| Offset | Attribute |", "|---|---|"]
            for i, attr in enumerate(chans, 1):
                lines.append(f"| +{i} | {attr} |")
            lines.append("")

    # ── Universe Maps ─────────────────────────────────────────────────────────
    lines += [
        "## Universe Maps",
        "",
        "Each DMX universe showing which fixture occupies which address range."
        "  Start and end addresses are 1-based.",
        "",
    ]

    for uni in sorted(by_universe):
        heads = sorted(by_universe[uni], key=lambda x: x['dmx_start'])
        occupied = set()

        lines += [f"### Universe {uni}", ""]
        lines += [
            "| Head | DMX Start | DMX End | Ch | Offset Map | Fixture |",
            "|---|---|---|---|---|---|",
        ]

        for h in heads:
            pid = h['personality_id']
            p   = data['personalities'].get(pid)
            if p:
                footprint    = p['chan_count']
                fixture_name = f"{p['model']} — {p['mode']}"
            else:
                footprint    = 0
                fixture_name = f"Personality {pid}"

            start = h['dmx_start']
            end   = start + footprint - 1 if footprint else start
            offset_map = f"{start}–{end}"

            for addr in range(start, start + footprint):
                occupied.add(addr)

            lines.append(
                f"| {h['head_num']} | {start} | {end} | {footprint} "
                f"| `{offset_map}` | {fixture_name} |"
            )

        lines.append("")

        # ASCII occupancy bar
        bar = _dmx_bar(occupied)
        used_pct = round(len(occupied) / 512 * 100, 1)
        lines += [
            f"**Universe {uni} occupancy** — {len(occupied)} / 512 channels used ({used_pct}%)",
            "",
            "```",
            f"  1{'':>30}256{'':>30}512",
            f"  {bar}",
            "```",
            "",
        ]

    # ── Fixture Groups ────────────────────────────────────────────────────────
    lines += [
        "## Fixture Groups",
        "",
        "Groups as defined in the show file.  Each group lists its members"
        " in DMX address order — useful for building group-based control logic.",
        "",
    ]

    dmx_to_head = {h['dmx_start']: h for h in data['heads'].values()}

    for g in data['groups']:
        if not g['name']:
            continue
        lines += [f"### {g['name']}", ""]
        lines += ["| Head | Uni | DMX Start | DMX End | Fixture |", "|---|---|---|---|---|"]
        for addr in g['dmx_addresses']:
            h = dmx_to_head.get(addr)
            if h:
                pid = h['personality_id']
                p   = data['personalities'].get(pid)
                if p:
                    footprint    = p['chan_count']
                    fixture_name = f"{p['model']}"
                else:
                    footprint    = 0
                    fixture_name = f"Personality {pid}"
                dmx_end = addr + footprint - 1 if footprint else addr
                lines.append(
                    f"| {h['head_num']} | {h['universe']} "
                    f"| {addr} | {dmx_end} | {fixture_name} |"
                )
            else:
                lines.append(f"| ? | ? | {addr} | ? | ? |")
        lines.append("")

    # ── Flat Channel Reference ────────────────────────────────────────────────
    lines += [
        "## Flat Channel Reference",
        "",
        "Every DMX channel in the rig, listed in universe and address order."
        "  This is the table to consult when you need to know: *what does"
        " Universe X, channel Y control?*",
        "",
        "| Uni | DMX Addr | Head | Attr | Fixture |",
        "|---|---|---|---|---|",
    ]

    # Build flat list: (universe, dmx_addr, head_num, attr_name, fixture_name)
    all_channels = []
    for h in data['heads'].values():
        pid = h['personality_id']
        p   = data['personalities'].get(pid)
        if not p or not p['channels']:
            continue
        chans = p['channels'][:p['chan_count']]
        for offset, attr in enumerate(chans):
            dmx_addr = h['dmx_start'] + offset
            all_channels.append((
                h['universe'],
                dmx_addr,
                h['head_num'],
                attr,
                p['model'],
            ))

    all_channels.sort(key=lambda x: (x[0], x[1]))

    for uni, addr, head_num, attr, fixture in all_channels:
        lines.append(f"| {uni} | {addr} | {head_num} | {attr} | {fixture} |")

    lines.append("")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Generate a DMX patch report HTML from a ChamSys .shw file."
    )
    ap.add_argument('shw_file', help='Path to the .shw file')
    ap.add_argument('--out', help='Output HTML path (default: <show>_patch.html alongside input)')
    args = ap.parse_args()

    shw_path = args.shw_file
    if not os.path.exists(shw_path):
        print(f"Error: {shw_path!r} not found", file=sys.stderr)
        sys.exit(1)

    show_base = os.path.splitext(os.path.basename(shw_path))[0]

    if args.out:
        out_path = args.out
    else:
        out_path = os.path.join(os.path.dirname(os.path.abspath(shw_path)),
                                show_base + "_patch.html")

    print(f"Parsing {shw_path} …")
    data = parse_shw(shw_path)

    print("Generating patch report …")
    markdown_text = generate_markdown(data, show_base)

    patched_pids  = {h['personality_id'] for h in data['heads'].values()}
    total_ch      = sum(
        p['chan_count']
        for pid, p in data['personalities'].items()
        if pid in patched_pids
    )
    badge     = f"{len(data['heads'])} heads · {total_ch} DMX channels"
    page_title = f"{show_base} — DMX Patch"
    html       = render_html(page_title, markdown_text, badge)

    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(html)
    print(f"Written: {out_path}")


if __name__ == '__main__':
    main()
