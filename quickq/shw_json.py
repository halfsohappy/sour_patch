#!/usr/bin/env python3
"""shw_json.py — Export a ChamSys .shw file as a comprehensive JSON document.

Produces a platform-neutral intermediate representation suitable for building
converters to other lighting control formats (GDTF, QLab, MA, Hog, ETC, etc.).

Output schema:
    meta         — source file, console version, save timestamp
    fixtures     — personalities (channel detail, defaults, wheel slots) + patched heads
    programming  — groups, palettes (with DMX values), FX waveforms (with matrices)
    show         — cue stacks (with DMX snapshots + FX slots), cues (with timing), executors

Usage:
    python quickq/shw_json.py path/to/show.shw
    python quickq/shw_json.py path/to/show.shw --out /path/to/output.json
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shw_common import parse_shw


# Deciseconds to seconds (timing values in ChamSys .shw are stored in 1/10s units)
def _ds_to_s(raw):
    return round(raw / 10.0, 1)


def build_json(show):
    """Transform the parsed show dict into the JSON output structure."""

    # ── Meta ──────────────────────────────────────────────────────────────────
    version = show.get('version') or {}
    comments = show.get('comments', [])
    saved_at = ''
    console  = version.get('product', '')
    for c in comments:
        if c.startswith('Time:'):
            saved_at = c[len('Time:'):].strip()

    meta = {
        'console':     console,
        'version_hex': version.get('version_hex', ''),
        'saved_at':    saved_at,
        'comments':    comments,
    }

    # ── Fixtures — personalities ───────────────────────────────────────────────
    personalities = []
    for pid, p in sorted(show['personalities'].items()):
        channels = []
        for ch in p.get('channels_detail', []):
            channels.append({
                'name':          ch['name'],
                'attr_type_id':  ch['attr_type_id'],
                'flags':         ch['flags'],
                'is_fine':       ch['is_fine'],
                'is_multi_cell': ch['is_multi_cell'],
            })

        # Convert defaults dict (int keys → str keys for JSON)
        defaults = {str(k): v for k, v in p.get('defaults', {}).items()}

        # Convert wheel_slots (int keys → str keys)
        wheel_slots = {
            str(k): v
            for k, v in p.get('wheel_slots', {}).items()
        }

        personalities.append({
            'id':           pid,
            'full_name':    p['full_name'],
            'manufacturer': p['manufacturer'],
            'model':        p['model'],
            'mode':         p['mode'],
            'chan_count':   p['chan_count'],
            'channels':     channels,
            'defaults':     defaults,
            'wheel_slots':  wheel_slots,
        })

    # ── Fixtures — patched heads ───────────────────────────────────────────────
    heads = []
    for hid, h in sorted(show['heads'].items()):
        heads.append({
            'head_id':        h['head_id'],
            'head_num':       h['head_num'],
            'universe':       h['universe'],
            'dmx_start':      h['dmx_start'],
            'personality_id': h['personality_id'],
        })

    # ── Programming — groups ──────────────────────────────────────────────────
    groups = []
    for g in show.get('groups', []):
        groups.append({
            'id':           g['id'],
            'name':         g['name'],
            'dmx_addresses': g['dmx_addresses'],
        })

    # ── Programming — palettes ────────────────────────────────────────────────
    palettes = []
    for p in show.get('palettes', []):
        # dmx_values has int keys; convert to str for JSON
        dmx_values = {str(k): v for k, v in p.get('dmx_values', {}).items()}
        palettes.append({
            'id':         p['id'],
            'type':       p['type'],
            'name':       p['name'],
            'dmx_values': dmx_values,
        })

    # ── Programming — FX waveforms ────────────────────────────────────────────
    fx_waveforms = []
    for w in show.get('fx', []):
        fx_waveforms.append({
            'id':            w['id'],
            'name':          w['name'],
            'num_attrs':     w.get('num_attrs', 0),
            'num_steps':     w.get('num_steps', 0),
            'speed':         w.get('speed', 0),
            'size':          w.get('size', 0),
            'attr_type_ids': w.get('attr_type_ids', []),
            'step_matrix':   w.get('step_matrix', []),
            'phase_matrix':  w.get('phase_matrix', []),
        })

    # ── Show — cue stacks ─────────────────────────────────────────────────────
    cue_stacks = []
    for s in show.get('cue_stacks', []):
        dmx_snapshot = {str(k): v for k, v in s.get('dmx_snapshot', {}).items()}
        cue_stacks.append({
            'id':           s['id'],
            'name':         s['name'],
            'dmx_snapshot': dmx_snapshot,
            'fx_slots':     s.get('fx_slots', []),
        })

    # ── Show — cues ───────────────────────────────────────────────────────────
    cues = []
    for c in show.get('cues', []):
        cues.append({
            'id':               c['id'],
            'name':             c['name'],
            'cue_number':       c.get('cue_number'),
            'stack_id':         c.get('stack_id', 0),
            'fade_in_s':        _ds_to_s(c.get('fade_in_raw', 0)),
            'fade_out_s':       _ds_to_s(c.get('fade_out_raw', 0)),
            'in_delay_s':       _ds_to_s(c.get('in_delay_raw', 0)),
            'behavioral_flags': c.get('behavioral_flags', 0),
        })

    # ── Show — executors ──────────────────────────────────────────────────────
    executors = []
    for e in show.get('executors', []):
        executors.append({
            'page':         e['page'],
            'button':       e['button'],
            'name':         e['name'],
            'cue_stack_id': e['cue_stack_id'],
        })

    return {
        'meta':        meta,
        'fixtures': {
            'personalities': personalities,
            'heads':         heads,
        },
        'programming': {
            'groups':       groups,
            'palettes':     palettes,
            'fx_waveforms': fx_waveforms,
        },
        'show': {
            'cue_stacks': cue_stacks,
            'cues':       cues,
            'executors':  executors,
        },
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description='Export ChamSys .shw to JSON.')
    ap.add_argument('shw',  help='Path to .shw file')
    ap.add_argument('--out', help='Output JSON path (default: <show>.json beside input)')
    args = ap.parse_args()

    if not os.path.isfile(args.shw):
        sys.exit(f'Error: file not found: {args.shw}')

    out_path = args.out or os.path.splitext(args.shw)[0] + '.json'

    print(f'Parsing {args.shw} ...')
    show = parse_shw(args.shw)

    print('Building JSON ...')
    doc = build_json(show)
    doc['meta']['source_file'] = os.path.basename(args.shw)

    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)

    # Print a quick summary
    p = doc['fixtures']['personalities']
    h = doc['fixtures']['heads']
    palettes  = doc['programming']['palettes']
    fx        = doc['programming']['fx_waveforms']
    stacks    = doc['show']['cue_stacks']
    cues      = doc['show']['cues']
    executors = doc['show']['executors']

    palette_with_values = sum(1 for x in palettes if x['dmx_values'])
    stacks_with_snap    = sum(1 for x in stacks if x['dmx_snapshot'])
    fx_with_matrix      = sum(1 for x in fx if x['step_matrix'])

    print(f'\nWrote {out_path}')
    print(f'  {len(p)} personalities  {len(h)} heads')
    print(f'  {len(palettes)} palettes  ({palette_with_values} with DMX values)')
    print(f'  {len(stacks)} cue stacks  ({stacks_with_snap} with DMX snapshot)')
    print(f'  {len(cues)} cues  {len(executors)} executors')
    print(f'  {len(fx)} FX waveforms  ({fx_with_matrix} with step matrix)')


if __name__ == '__main__':
    main()
