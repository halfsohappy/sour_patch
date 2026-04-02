"""shw_common.py -- Shared parser and HTML renderer for ChamSys .shw files.

Record type reference:
    \\   Comment lines
    V   Version / console info
    T   Show settings (large multi-line block)
    P   Fixture personality definition (channels, attributes)
    H   Patched head / fixture instance
    G   Fixture group
    F   Palette  (prefix a=position, b=colour, c=beam)
    W   FX waveform
    S   Cue stack
    C   Cue
    R   Cue stack step
    N   Executor / playback assignment
    B   Exec grid button assignment
    M/D/J/Q  Console settings
"""

import re
import os
import sys

try:
    import markdown as md_lib
except ImportError:
    sys.exit("Error: 'markdown' package not found.  Run: pip install markdown")


# ── CSV parser ───────────────────────────────────────────────────────────────

def _parse_csv(s):
    """Parse a comma-separated line that may contain quoted strings.

    Strips a trailing semicolon and/or trailing comma before parsing.
    Returns a list of field strings (quotes are stripped from the result).
    """
    s = s.strip()
    if s.endswith(';'):
        s = s[:-1]
    s = s.rstrip(',')

    fields = []
    current = []
    in_quote = False
    for ch in s:
        if ch == '"':
            in_quote = not in_quote
        elif ch == ',' and not in_quote:
            fields.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        fields.append(''.join(current))
    return fields


def _field(fields, idx, default=''):
    return fields[idx] if idx < len(fields) else default


def _hex(s, default=0):
    try:
        return int(s.strip(), 16)
    except (ValueError, AttributeError):
        return default


def _dec(s, default=0):
    try:
        return int(s.strip(), 10)
    except (ValueError, AttributeError):
        return default


def _is_hex_str(s):
    """Return True if s is a non-empty valid hexadecimal string."""
    try:
        int(s.strip(), 16)
        return True
    except (ValueError, AttributeError):
        return False


# ── .shw parser ──────────────────────────────────────────────────────────────

def parse_shw(filepath):
    """Parse a ChamSys QuickQ / MagicQ .shw file.

    Returns a dict with keys:
        comments        list of comment strings
        version         dict or None
        personalities   dict  pid (int) -> personality dict
        heads           dict  head_id (int) -> head dict
        groups          list of group dicts
        palettes        list of palette dicts
        cue_stacks      list of cue-stack dicts
        cues            list of cue dicts
        executors       list of executor dicts
        fx              list of FX waveform dicts
    """
    with open(filepath, encoding='latin-1') as fh:
        raw_lines = fh.read().splitlines()

    # ── Group lines into record blocks ────────────────────────────────────────
    # A new record starts when a line begins with a single uppercase letter
    # followed by a comma, e.g. "P," "H," "S,".
    # Continuation lines (numeric data, quoted strings) are appended to the
    # current record.  Comment lines (\) are standalone.

    blocks = []
    current = None

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue

        if line.startswith('\\'):
            blocks.append({'type': '\\', 'lines': [line]})
            continue

        m = re.match(r'^([A-Z]),', line)
        if m:
            current = {'type': m.group(1), 'lines': [line]}
            blocks.append(current)
        elif current is not None:
            current['lines'].append(line)

    # ── Parse each block ──────────────────────────────────────────────────────

    result = {
        'comments':      [],
        'version':       None,
        'personalities': {},
        'heads':         {},
        'groups':        [],
        'palettes':      [],
        'cue_stacks':    [],
        'cues':          [],
        'executors':     [],
        'fx':            [],
    }

    for block in blocks:
        t     = block['type']
        lines = block['lines']

        # ── Comments ──────────────────────────────────────────────────────────
        if t == '\\':
            result['comments'].append(lines[0][1:].strip() if len(lines[0]) > 1 else '')

        # ── Version ───────────────────────────────────────────────────────────
        elif t == 'V':
            f = _parse_csv(lines[0])
            result['version'] = {
                'version_hex': _field(f, 1),
                'product':     _field(f, 2),
            }

        # ── Personality (P) ───────────────────────────────────────────────────
        elif t == 'P':
            f = _parse_csv(lines[0])
            if len(f) < 2:
                continue
            pid          = _hex(_field(f, 1))
            full_name    = _field(f, 2)
            manufacturer = _field(f, 3)
            mode         = _field(f, 4)
            model        = _field(f, 5)

            # Line 2: first field is channel count (hex)
            chan_count = 0
            if len(lines) > 1:
                f2 = _parse_csv(lines[1])
                if f2:
                    chan_count = _hex(f2[0])

            # Channel lines: "name",flags_hex,attr_type_id_hex,
            # Defaults line: chan_count_hex,idx0,val0,idx1,val1,...
            # Wheel slot lines: chan_idx_hex,"slot_name",range_low,range_high,...
            channels = []
            channels_detail = []
            defaults = {}
            wheel_slots = {}

            for cl in lines[2:]:
                cl = cl.strip()
                if not cl:
                    continue
                cf = _parse_csv(cl)
                if not cf:
                    continue

                if cl.startswith('"'):
                    # Channel line
                    name = cf[0] if cf else ''
                    if name:
                        channels.append(name)
                    if len(cf) >= 3:
                        flags = _hex(cf[1])
                        channels_detail.append({
                            'name':          name,
                            'flags':         flags,
                            'attr_type_id':  _hex(cf[2]),
                            'is_fine':       bool(flags & 0x08),
                            'is_multi_cell': bool(flags & 0x40000000),
                        })
                elif len(cf) >= 2 and not _is_hex_str(cf[1]):
                    # Wheel slot line: chan_idx,"slot_name",range_low,range_high,...
                    chan_idx = _hex(cf[0])
                    slot_name = cf[1]
                    range_low  = _hex(cf[2]) if len(cf) > 2 else 0
                    range_high = _hex(cf[3]) if len(cf) > 3 else 255
                    wheel_slots.setdefault(chan_idx, []).append({
                        'name':       slot_name,
                        'range_low':  range_low,
                        'range_high': range_high,
                    })
                elif len(cf) >= 3:
                    # Defaults line: chan_count,idx0,val0,idx1,val1,...
                    for i in range(1, len(cf) - 1, 2):
                        defaults[_hex(cf[i])] = _hex(cf[i + 1])

            result['personalities'][pid] = {
                'id':              pid,
                'full_name':       full_name,
                'manufacturer':    manufacturer,
                'mode':            mode,
                'model':           model,
                'chan_count':      chan_count,
                'channels':        channels,
                'channels_detail': channels_detail,
                'defaults':        defaults,
                'wheel_slots':     wheel_slots,
            }

        # ── Head / fixture instance (H) ───────────────────────────────────────
        elif t == 'H':
            # Line 1:  H,head_id,universe,...
            # Last line (ends with ;):  dmx_start_hex,head_num_hex,personality_id_dec,...,;
            f1 = _parse_csv(lines[0])
            if len(f1) < 3:
                continue
            head_id  = _hex(_field(f1, 1))
            universe = _dec(_field(f1, 2))

            # Find the terminating line
            summary = None
            for l in reversed(lines):
                stripped = l.strip()
                if stripped.endswith(';'):
                    summary = stripped
                    break

            dmx_start     = 0
            head_num      = 0
            personality_id = 0
            if summary:
                sf = _parse_csv(summary)
                if len(sf) >= 3:
                    dmx_start      = _hex(sf[0])
                    head_num       = _hex(sf[1])
                    personality_id = _dec(sf[2])

            result['heads'][head_id] = {
                'head_id':       head_id,
                'universe':      universe,
                'dmx_start':     dmx_start,
                'head_num':      head_num,
                'personality_id': personality_id,
            }

        # ── Group (G) ──────────────────────────────────────────────────────────
        elif t == 'G':
            f = _parse_csv(lines[0])
            if len(f) < 4:
                continue
            gid   = _hex(_field(f, 1))
            name  = _field(f, 2)
            count = _dec(_field(f, 3))
            addrs = []
            for i in range(4, 4 + count):
                if i < len(f):
                    addrs.append(_hex(f[i]))
            result['groups'].append({
                'id':            gid,
                'name':          name,
                'dmx_addresses': addrs,
            })

        # ── Palette (F) ────────────────────────────────────────────────────────
        elif t == 'F':
            f = _parse_csv(lines[0])
            if len(f) < 3:
                continue
            pid_hex = _field(f, 1).lower()
            name    = _field(f, 2)
            prefix  = pid_hex[0] if pid_hex else ''
            ptype   = {'a': 'position', 'b': 'colour', 'c': 'beam'}.get(prefix, 'other')

            # Parse DMX value triplets: addr_hex, val_hex, flag_int
            # Triplets continue across continuation lines until footer (8-digit hex).
            dmx_values = {}
            tokens = []
            for ln in lines[1:]:
                for tok in ln.split(','):
                    tok = tok.strip().rstrip(';')
                    if tok:
                        tokens.append(tok)
            i = 0
            while i + 2 < len(tokens):
                a_s, v_s, fl_s = tokens[i], tokens[i + 1], tokens[i + 2]
                # Footer: large packed value (>= 6 hex digits) or value > 0xFFFF
                if len(a_s) >= 6 or _hex(a_s) > 0xFFFF or _hex(v_s) > 0xFF:
                    break
                addr = _hex(a_s)
                val  = _hex(v_s)
                flag = _hex(fl_s)
                if addr > 0:
                    dmx_values[addr] = {'value': val, 'flag': flag}
                i += 3

            result['palettes'].append({
                'id':         pid_hex,
                'type':       ptype,
                'name':       name,
                'dmx_values': dmx_values,
            })

        # ── Cue (C) ────────────────────────────────────────────────────────────
        elif t == 'C':
            f = _parse_csv(lines[0])
            if len(f) < 3:
                continue
            cid  = _hex(_field(f, 1))
            name = _field(f, 2)

            behavioral_flags = 0
            fade_in_raw  = 0
            fade_out_raw = 0
            in_delay_raw = 0
            stack_id     = 0
            cue_number   = None

            if len(lines) > 1:
                f2 = _parse_csv(lines[1])
                behavioral_flags = _hex(_field(f2, 0))
                fade_in_raw      = _hex(_field(f2, 1))
                fade_out_raw     = _hex(_field(f2, 2))
                in_delay_raw     = _hex(_field(f2, 3))

            if len(lines) > 2:
                f3 = _parse_csv(lines[2])
                stack_id = _hex(_field(f3, 2))
                for fld in f3:
                    if '.' in fld:
                        try:
                            cue_number = float(fld)
                            break
                        except ValueError:
                            pass

            result['cues'].append({
                'id':               cid,
                'name':             name,
                'stack_id':         stack_id,
                'cue_number':       cue_number,
                'behavioral_flags': behavioral_flags,
                'fade_in_raw':      fade_in_raw,
                'fade_out_raw':     fade_out_raw,
                'in_delay_raw':     in_delay_raw,
            })

        # ── Cue stack (S) ──────────────────────────────────────────────────────
        elif t == 'S':
            f = _parse_csv(lines[0])
            if len(f) < 3:
                continue
            sid    = _hex(_field(f, 1))
            name   = _field(f, 2)
            num_fx = _hex(_field(f, 4))

            dmx_snapshot = {}
            fx_slots     = []

            # lines[1] = timing/reserved (10 zero fields) — skip
            # lines[2+] = DMX pairs, then FX slots, then footer
            state          = 'dmx'
            remaining      = 0
            current_fx     = None

            for ln in lines[2:]:
                ln = ln.strip()
                if not ln:
                    continue
                cf2 = _parse_csv(ln)
                if not cf2:
                    continue
                first = cf2[0].strip()

                # Footer: 8-digit hex value with high bits set
                if len(first) == 8 and _is_hex_str(first) and _hex(first) > 0xFFFF:
                    break

                if state == 'dmx':
                    # FX header: 12 fields, speed (cf2[2]) is 4-digit zero-padded hex
                    spd_s = cf2[2].strip() if len(cf2) > 2 else ''
                    if (len(cf2) == 12 and len(spd_s) == 4
                            and spd_s[0] == '0' and _is_hex_str(spd_s)):
                        current_fx = {
                            'waveform_id': _hex(cf2[0]),
                            'num_heads':   _hex(cf2[1]),
                            'speed':       _hex(cf2[2]),
                            'size':        _hex(cf2[5]),
                            'assignments': [],
                        }
                        fx_slots.append(current_fx)
                        remaining = current_fx['num_heads']
                        if remaining > 0:
                            state = 'fx_assign'
                    else:
                        # DMX pairs: addr, val, addr, val, ...
                        for i in range(0, len(cf2) - 1, 2):
                            addr = _hex(cf2[i])
                            val  = _hex(cf2[i + 1])
                            if addr > 0 and val <= 0xFF:
                                dmx_snapshot[addr] = val

                elif state == 'fx_assign':
                    if len(cf2) >= 7:
                        current_fx['assignments'].append({
                            'addr':  _hex(cf2[0]),
                            'phase': _hex(cf2[4]),
                            'flags': _hex(cf2[6]),
                        })
                        remaining -= 1
                        if remaining <= 0:
                            state = 'dmx'

            result['cue_stacks'].append({
                'id':           sid,
                'name':         name,
                'dmx_snapshot': dmx_snapshot,
                'fx_slots':     fx_slots,
            })

        # ── Executor / playback (N) ────────────────────────────────────────────
        elif t == 'N':
            # N,page,button,flags,type,cue_stack_id,...,"name",...,;
            f = _parse_csv(lines[0])
            if len(f) < 6:
                continue
            page       = _hex(_field(f, 1))
            button     = _hex(_field(f, 2))
            stack_id   = _hex(_field(f, 5))
            name       = _field(f, 8) if len(f) > 8 else ''
            result['executors'].append({
                'page':          page,
                'button':        button,
                'name':          name,
                'cue_stack_id':  stack_id,
            })

        # ── FX waveform (W) ────────────────────────────────────────────────────
        elif t == 'W':
            f = _parse_csv(lines[0])
            if len(f) < 3:
                continue
            wid       = _hex(_field(f, 1))
            name      = _field(f, 2)
            num_attrs = _hex(_field(f, 4))
            num_steps = _hex(_field(f, 5))
            speed     = _hex(_field(f, 10))
            size      = _hex(_field(f, 11))
            attr_type_ids = [
                _hex(_field(f, 7 + i))
                for i in range(num_attrs)
            ]

            # Collect matrix rows from continuation lines.
            # First num_steps rows = step_matrix, next num_steps = phase_matrix.
            # Stop at footer (8-digit hex).
            matrix_rows = []
            for ln in lines[1:]:
                ln = ln.strip()
                if not ln:
                    continue
                cf2 = _parse_csv(ln)
                if not cf2:
                    continue
                first = cf2[0].strip()
                if len(first) == 8 and _is_hex_str(first):
                    break
                row = [_hex(v) for v in cf2 if v.strip() and _is_hex_str(v.strip())]
                if row:
                    matrix_rows.append(row)

            step_matrix  = matrix_rows[:num_steps]
            phase_matrix = matrix_rows[num_steps: num_steps * 2] if len(matrix_rows) >= num_steps * 2 else []

            result['fx'].append({
                'id':            wid,
                'name':          name,
                'num_attrs':     num_attrs,
                'num_steps':     num_steps,
                'speed':         speed,
                'size':          size,
                'attr_type_ids': attr_type_ids,
                'step_matrix':   step_matrix,
                'phase_matrix':  phase_matrix,
            })

    return result


# ── HTML renderer (same design language as docs/build_docs.py) ───────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Martian+Mono:wght@300;400;500&family=Playwrite+DE+Grund:wght@400;500&display=swap');

:root {
  --bg:           #ffffff;
  --bg-alt:       #f4f5f6;
  --bg-card:      #ffffff;
  --bg-hover:     #eef0f3;
  --border:       #dddddd;
  --text-dark:    #2A2F36;
  --text-medium:  #6C7A89;
  --text-light:   #ABB7B7;
  --accent:       #90849c;
  --accent-hover: #7a6f8a;
  --accent-dim:   rgba(144,132,156,0.12);
  --header-bg:    #DAC7FF;
  --header-text:  #2A2F36;
  --radius:       5px;
  --radius-lg:    8px;
  --shadow:       0 1px 4px rgba(0,0,0,0.08);
  --shadow-md:    0 2px 8px rgba(0,0,0,0.1);
  --font:         "Martian Mono", monospace;
  --font-title:   "Playwrite DE Grund", cursive;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: var(--font);
  font-size: 18px;
  font-weight: 300;
  color: var(--text-dark);
  background: var(--bg-alt);
  line-height: 1.6;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.docs-header {
  background: var(--header-bg);
  box-shadow: 0 2px 6px rgba(0,0,0,0.10);
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: stretch;
  min-height: 64px;
}

.hdr-logo-box {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 8px 22px;
  border-right: 1px solid rgba(0,0,0,0.10);
  gap: 2px;
  text-decoration: none;
}

.hdr-logo-text {
  font-family: var(--font-title);
  font-size: 22px;
  font-weight: 300;
  color: var(--header-text);
  letter-spacing: -0.02em;
  line-height: 1.1;
}

.hdr-logo-sub {
  font-family: var(--font);
  font-size: 9px;
  font-weight: 400;
  color: rgba(0,0,0,0.42);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  line-height: 1.35;
}

.hdr-title {
  display: flex;
  align-items: center;
  padding: 0 20px;
  font-family: var(--font-title);
  font-size: 16px;
  font-weight: 400;
  color: var(--header-text);
  flex: 1;
}

.hdr-badge {
  display: flex;
  align-items: center;
  padding: 0 18px;
  font-family: var(--font);
  font-size: 12px;
  font-weight: 400;
  color: rgba(0,0,0,0.50);
  border-left: 1px solid rgba(0,0,0,0.10);
  white-space: nowrap;
}

.docs-layout {
  display: flex;
  flex: 1;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
  padding: 32px 16px;
  gap: 32px;
  align-items: flex-start;
}

.docs-toc {
  width: 260px;
  flex-shrink: 0;
  position: sticky;
  top: 96px;
  max-height: calc(100vh - 112px);
  overflow-y: auto;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 0 20px;
  box-shadow: var(--shadow);
}

.docs-toc-heading {
  font-family: var(--font);
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-light);
  padding: 0 16px 8px;
}

.docs-toc .toc { list-style: none; padding: 0; margin: 0; }
.docs-toc .toc li { list-style: none; padding: 0; margin: 0; }

.docs-toc .toc a {
  display: block;
  font-family: var(--font);
  font-size: 13px;
  font-weight: 300;
  color: var(--text-medium);
  text-decoration: none;
  padding: 4px 16px;
  border-left: 2px solid transparent;
  transition: color 0.12s, background 0.12s;
}

.docs-toc .toc a:hover    { color: var(--accent); background: var(--accent-dim); }
.docs-toc .toc a.toc-active {
  color: var(--accent);
  border-left-color: var(--accent);
  background: var(--accent-dim);
  font-weight: 400;
}

.docs-toc .toc ul          { list-style: none; padding: 0; margin: 0; }
.docs-toc .toc ul a        { padding-left: 28px; font-size: 12px; }
.docs-toc .toc ul ul a     { padding-left: 42px; font-size: 11px; color: var(--text-light); }

.docs-content { flex: 1; min-width: 0; }

.docs-article {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 40px 48px;
  box-shadow: var(--shadow-md);
}

.docs-article h1 {
  font-family: var(--font-title);
  font-size: 28px;
  font-weight: 400;
  color: var(--header-text);
  margin-bottom: 8px;
  line-height: 1.2;
}

.docs-article h2 {
  font-family: var(--font-title);
  font-size: 22px;
  font-weight: 400;
  color: var(--text-dark);
  margin-top: 40px;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--header-bg);
}

.docs-article h2:first-child { margin-top: 0; }

.docs-article h3 {
  font-family: var(--font-title);
  font-size: 20px;
  font-weight: 700;
  color: var(--accent);
  margin-top: 24px;
  margin-bottom: 8px;
}

.docs-article h4 {
  font-family: var(--font);
  font-size: 19px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-medium);
  margin-top: 20px;
  margin-bottom: 6px;
}

.docs-article p {
  font-size: 16px;
  font-weight: 300;
  line-height: 1.75;
  margin-bottom: 14px;
}

.docs-article ul,
.docs-article ol {
  font-size: 16px;
  font-weight: 300;
  line-height: 1.7;
  padding-left: 24px;
  margin-bottom: 14px;
}

.docs-article li          { margin-bottom: 4px; }
.docs-article li > ul,
.docs-article li > ol     { margin-bottom: 0; margin-top: 4px; }

.docs-article strong { font-weight: 500; }
.docs-article em     { font-style: italic; color: var(--text-medium); }

.docs-article hr {
  border: none;
  border-top: 1px solid var(--border);
  margin: 32px 0;
}

.docs-article code {
  font-family: var(--font);
  font-size: 14px;
  background: var(--accent-dim);
  color: var(--accent);
  border-radius: 3px;
  padding: 1px 5px;
}

.docs-article pre {
  background: var(--bg-alt);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  overflow-x: auto;
  margin-bottom: 16px;
}

.docs-article pre code {
  background: none;
  color: var(--text-dark);
  padding: 0;
  font-size: 13px;
  font-weight: 300;
  line-height: 1.6;
}

.docs-article table {
  width: 100%;
  border-collapse: collapse;
  font-size: 15px;
  margin-bottom: 20px;
}

.docs-article thead th {
  text-align: left;
  font-size: 12px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-light);
  padding: 8px 12px;
  border-bottom: 2px solid var(--border);
  background: var(--bg-alt);
}

.docs-article tbody td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  line-height: 1.6;
}

.docs-article tbody tr:hover td { background: var(--accent-dim); }

.docs-article blockquote {
  border-left: 3px solid var(--header-bg);
  margin: 16px 0;
  padding: 10px 20px;
  background: var(--accent-dim);
  border-radius: 0 var(--radius) var(--radius) 0;
  font-size: 15px;
  color: var(--text-medium);
}

.docs-article blockquote p { margin-bottom: 0; font-size: 15px; }

.docs-article .headerlink {
  font-size: 14px;
  color: var(--text-light);
  text-decoration: none;
  margin-left: 6px;
  opacity: 0;
  transition: opacity 0.15s;
}

.docs-article h1:hover .headerlink,
.docs-article h2:hover .headerlink,
.docs-article h3:hover .headerlink,
.docs-article h4:hover .headerlink { opacity: 1; }

.docs-footer {
  text-align: center;
  padding: 24px;
  font-size: 12px;
  color: var(--text-light);
  border-top: 1px solid var(--border);
  margin-top: 16px;
}

.docs-footer a { color: var(--accent); text-decoration: none; }
.docs-footer a:hover { text-decoration: underline; }

@media (max-width: 860px) {
  .docs-toc      { display: none; }
  .docs-article  { padding: 24px 20px; }
  .docs-layout   { padding: 16px 12px; }
}
"""

JS = """
(function () {
  "use strict";
  var headings = Array.from(document.querySelectorAll(
    ".docs-article h1, .docs-article h2, .docs-article h3"
  ));
  var tocLinks = Array.from(document.querySelectorAll(".docs-toc a"));
  if (!headings.length || !tocLinks.length) { return; }

  function setActive(id) {
    tocLinks.forEach(function (a) {
      a.classList.toggle("toc-active", a.getAttribute("href") === "#" + id);
    });
  }

  window.addEventListener("scroll", function () {
    var scrollY = window.scrollY;
    var active = headings[0];
    headings.forEach(function (h) {
      if (h.offsetTop - 100 <= scrollY) { active = h; }
    });
    if (active) { setActive(active.id); }
  }, { passive: true });
})();
"""


def render_html(page_title, markdown_text, badge_text=''):
    """Render a markdown string to a complete styled HTML page.

    Uses the same design language as docs/build_docs.py.
    """
    md = md_lib.Markdown(
        extensions=['toc', 'fenced_code', 'tables', 'attr_list'],
        extension_configs={'toc': {'permalink': True, 'toc_depth': '2-3'}},
    )
    content_html = md.convert(markdown_text)
    toc_html = getattr(md, 'toc', '')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{page_title}</title>
  <style>{CSS}</style>
</head>
<body>

  <header class="docs-header">
    <div class="hdr-logo-box">
      <span class="hdr-logo-text">QuickQ</span>
      <span class="hdr-logo-sub">Show File Report</span>
    </div>
    <div class="hdr-title">{page_title}</div>
    {f'<div class="hdr-badge">{badge_text}</div>' if badge_text else ''}
  </header>

  <div class="docs-layout">
    <nav class="docs-toc">
      <div class="docs-toc-heading">Contents</div>
      {toc_html}
    </nav>
    <main class="docs-content">
      <article class="docs-article">
        {content_html}
      </article>
    </main>
  </div>

  <footer class="docs-footer">
    Generated from a ChamSys QuickQ .shw file
  </footer>

  <script>{JS}</script>
</body>
</html>
"""
