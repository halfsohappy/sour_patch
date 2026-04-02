#!/usr/bin/env python3
"""shw_show.py — Full human-readable show report for ChamSys QuickQ / MagicQ .shw files.

Covers everything in the file: fixture rig, patch, groups, palettes, cue stacks,
cues, executors, and FX waveforms.  Output is a self-contained HTML page that
uses the same design language as the TheaterGWD docs site.

Usage:
    python quickq/shw_show.py path/to/show.shw
    python quickq/shw_show.py path/to/show.shw --out /path/to/report.html

Requires: pip install markdown
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shw_common import parse_shw, render_html


# ── Markdown generation ───────────────────────────────────────────────────────

def _show_name_from_comments(comments):
    """Try to pull a clean show name from the comment block."""
    for c in comments:
        if 'Show saved as' in c:
            # e.g. "Show saved as '/Users/.../QuickQDemo.shw'"
            parts = c.split("'")
            if len(parts) >= 2:
                return os.path.splitext(os.path.basename(parts[1]))[0]
    return None


def generate_markdown(data, fallback_name):
    show_name = _show_name_from_comments(data['comments']) or fallback_name
    lines = []

    # ── Title ─────────────────────────────────────────────────────────────────
    lines += [
        f"# {show_name}",
        "",
        "Full show report generated from a ChamSys QuickQ `.shw` file.",
        "",
    ]

    # Show metadata from comment block
    for c in data['comments']:
        if c.startswith('ChamSys') or c.startswith('QuickQ'):
            lines.append(f"> **Console software:** {c}")
        elif 'Show saved as' in c:
            path = c.replace('Show saved as ', '').strip("'")
            lines.append(f"> **File:** `{path}`")
        elif c.startswith('Time:'):
            lines.append(f"> **Saved:** {c[6:].strip()}")
    lines += ["", ""]

    # ── Quick Stats ────────────────────────────────────────────────────────────
    lines += ["## Quick Stats", ""]

    pers_head_counts = {}
    for h in data['heads'].values():
        pid = h['personality_id']
        pers_head_counts[pid] = pers_head_counts.get(pid, 0) + 1

    universes = sorted({h['universe'] for h in data['heads'].values()})

    pos_count  = sum(1 for p in data['palettes'] if p['type'] == 'position')
    col_count  = sum(1 for p in data['palettes'] if p['type'] == 'colour')
    beam_count = sum(1 for p in data['palettes'] if p['type'] == 'beam')

    named_cues   = [c for c in data['cues']       if c['name']]
    named_stacks = [s for s in data['cue_stacks'] if s['name']]
    named_execs  = [e for e in data['executors']  if e['name']]

    lines += [
        "| | |",
        "|---|---|",
        f"| **Fixture types** | {len(data['personalities'])} |",
        f"| **Patched heads** | {len(data['heads'])} |",
        f"| **DMX universes** | {len(universes)} ({', '.join(str(u) for u in universes)}) |",
        f"| **Groups** | {len(data['groups'])} |",
        f"| **Position palettes** | {pos_count} |",
        f"| **Colour palettes** | {col_count} |",
        f"| **Beam palettes** | {beam_count} |",
        f"| **Cue stacks** | {len(named_stacks)} named |",
        f"| **Cues** | {len(named_cues)} named |",
        f"| **Executors** | {len(named_execs)} assigned |",
        f"| **FX waveforms** | {len(data['fx'])} |",
        "",
    ]

    # ── Fixture Rig ────────────────────────────────────────────────────────────
    lines += [
        "## Fixture Rig",
        "",
        "Each fixture personality patched in this show, with its full DMX channel list.",
        "",
    ]

    for pid, p in sorted(data['personalities'].items()):
        count = pers_head_counts.get(pid, 0)
        if count == 0:
            continue  # unpatched personality — skip

        lines += [
            f"### {p['model']}",
            "",
            f"**Manufacturer:** {p['manufacturer']}  ",
            f"**Mode:** {p['mode']}  ",
            f"**DMX footprint:** {p['chan_count']} ch  ",
            f"**Patched:** {count} fixture{'s' if count != 1 else ''}",
            "",
        ]

        # Channel attribute table (limit to actual channel count)
        chans = p['channels'][:p['chan_count']]
        if chans:
            lines += ["| Ch | Attribute |", "|---|---|"]
            for i, attr in enumerate(chans, 1):
                lines.append(f"| {i} | {attr} |")
            lines.append("")

    # ── Patch ─────────────────────────────────────────────────────────────────
    lines += [
        "## Patch",
        "",
        "All patched fixtures, organized by DMX universe.",
        "",
    ]

    by_universe: dict = {}
    for h in data['heads'].values():
        by_universe.setdefault(h['universe'], []).append(h)

    for uni in sorted(by_universe):
        heads = sorted(by_universe[uni], key=lambda x: x['dmx_start'])
        lines += [f"### Universe {uni}", ""]
        lines += ["| Head | DMX Start | DMX End | Ch | Fixture |", "|---|---|---|---|---|"]
        for h in heads:
            pid = h['personality_id']
            p   = data['personalities'].get(pid)
            if p:
                footprint    = p['chan_count']
                fixture_name = f"{p['model']} — {p['mode']}"
            else:
                footprint    = '?'
                fixture_name = f"Personality {pid}" if pid else '(unpatched)'
            dmx_end = (h['dmx_start'] + footprint - 1) if isinstance(footprint, int) else '?'
            lines.append(
                f"| {h['head_num']} | {h['dmx_start']} | {dmx_end} "
                f"| {footprint} | {fixture_name} |"
            )
        lines.append("")

    # ── Groups ────────────────────────────────────────────────────────────────
    lines += [
        "## Groups",
        "",
        "Fixture groupings as defined on the console.",
        "",
    ]

    # Build a lookup: DMX start address -> head record
    dmx_to_head = {h['dmx_start']: h for h in data['heads'].values()}

    for g in data['groups']:
        if not g['name']:
            continue
        lines += [f"### {g['name']}", ""]
        lines += ["| Head | Universe | DMX Start | DMX End | Fixture |", "|---|---|---|---|---|"]
        for addr in g['dmx_addresses']:
            h = dmx_to_head.get(addr)
            if h:
                pid = h['personality_id']
                p   = data['personalities'].get(pid)
                if p:
                    footprint    = p['chan_count']
                    fixture_name = f"{p['model']}"
                else:
                    footprint    = '?'
                    fixture_name = f"Personality {pid}"
                dmx_end = (addr + footprint - 1) if isinstance(footprint, int) else '?'
                lines.append(
                    f"| {h['head_num']} | {h['universe']} | {addr} | {dmx_end} | {fixture_name} |"
                )
            else:
                lines.append(f"| ? | ? | {addr} | ? | ? |")
        lines.append("")

    # ── Palettes ──────────────────────────────────────────────────────────────
    lines += [
        "## Palettes",
        "",
        "Stored looks.  `a` prefix = position, `b` = colour, `c` = beam.",
        "",
    ]

    for label, ptype in [("Position", "position"), ("Colour", "colour"), ("Beam", "beam")]:
        pals = [p for p in data['palettes'] if p['type'] == ptype]
        if not pals:
            continue
        lines += [f"### {label} Palettes", ""]
        for p in pals:
            lines.append(f"- `{p['id']}` — **{p['name']}**")
        lines.append("")

    # ── Cue Stacks & Cues ─────────────────────────────────────────────────────
    lines += [
        "## Cue Stacks & Cues",
        "",
        "The console organizes playback into *cue stacks* (a sequence of steps)"
        " and individual *cues* (snapshots of fixture state).",
        "",
    ]

    if named_stacks:
        lines += ["### Cue Stacks", ""]
        lines += ["| ID | Name |", "|---|---|"]
        for s in named_stacks:
            lines.append(f"| {s['id']} | {s['name']} |")
        lines.append("")

    if named_cues:
        lines += ["### Cues", ""]
        lines += ["| ID | Name |", "|---|---|"]
        for c in named_cues:
            lines.append(f"| {c['id']} | {c['name']} |")
        lines.append("")

    # ── Executors ─────────────────────────────────────────────────────────────
    lines += [
        "## Executors",
        "",
        "Playback buttons on the console surface.  Each executor is assigned to a"
        " cue stack; pressing its button runs that stack.",
        "",
    ]

    if named_execs:
        stack_by_id = {s['id']: s['name'] for s in data['cue_stacks']}
        lines += ["| Page | Button | Label | Cue Stack |", "|---|---|---|---|"]
        for e in sorted(named_execs, key=lambda x: (x['page'], x['button'])):
            stack_name = stack_by_id.get(e['cue_stack_id'], f"Stack {e['cue_stack_id']}")
            lines.append(
                f"| {e['page']} | {e['button'] + 1} | {e['name']} | {stack_name} |"
            )
        lines.append("")
    else:
        lines += ["*No named executors found.*", ""]

    # ── FX Waveforms ──────────────────────────────────────────────────────────
    if data['fx']:
        lines += [
            "## FX Waveforms",
            "",
            "Custom effect patterns defined in this show.",
            "",
            "| ID | Name |",
            "|---|---|",
        ]
        for fx in sorted(data['fx'], key=lambda x: x['id']):
            lines.append(f"| {fx['id']} | {fx['name']} |")
        lines.append("")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Generate a full show report HTML from a ChamSys .shw file."
    )
    ap.add_argument('shw_file', help='Path to the .shw file')
    ap.add_argument('--out', help='Output HTML path (default: <show>_show.html alongside input)')
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
                                show_base + "_show.html")

    print(f"Parsing {shw_path} …")
    data = parse_shw(shw_path)

    print("Generating report …")
    markdown_text = generate_markdown(data, show_base)
    page_title    = f"{show_base} — Full Show Report"
    badge         = f"{len(data['heads'])} heads · {len(data['cues'])} cues"
    html          = render_html(page_title, markdown_text, badge)

    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(html)
    print(f"Written: {out_path}")


if __name__ == '__main__':
    main()
