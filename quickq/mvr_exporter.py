#!/usr/bin/env python3
"""mvr_exporter.py — Export a JSON intermediate file to MVR (My Virtual Rig).

Reads the platform-neutral JSON produced by shw_json.py and writes a .mvr file
containing GeneralSceneDescription.xml and one .gdtf stub per fixture type.

MVR is the first export spoke of the sour_patch interoperability hub.

Usage:
    python quickq/mvr_exporter.py path/to/show.json
    python quickq/mvr_exporter.py path/to/show.json --out /path/to/output.mvr
"""

import argparse
import io
import json
import os
import sys
import uuid
import zipfile
import xml.etree.ElementTree as ET


# ── ChamSys attr_type_id → GDTF Attribute mapping ───────────────────────────

ATTR_TYPE_MAP = {
    0x00: "Dimmer",
    0x02: "Shutter1",
    0x03: "Iris",
    0x04: "Pan",
    0x05: "Tilt",
    0x06: "Color1",
    0x08: "Gobo1",
    0x09: "Gobo2",
    0x0A: "Gobo1Pos",
    0x0B: "Gobo2Pos",
    0x0C: "Focus",
    0x0D: "Zoom",
    0x0E: "Prism1",
    0x10: "ColorAdd_R",
    0x11: "ColorAdd_G",
    0x12: "ColorAdd_B",
    0x13: "ColorAdd_W",
    0x18: "CTC",
    0x19: "CTO",
    0x1B: "ColorAdd_A",
    0x20: "ColorSub_C",
    0x21: "ColorSub_M",
    0x22: "ColorSub_Y",
    0x33: "XYZ_Z",
}

# GDTF Attribute → (FeatureGroup, Feature) mapping
ATTR_FEATURE_MAP = {
    "Dimmer":     ("Dimmer",   "Dimmer"),
    "Shutter1":   ("Beam",     "Beam"),
    "Iris":       ("Beam",     "Beam"),
    "Pan":        ("Position", "PanTilt"),
    "Tilt":       ("Position", "PanTilt"),
    "Color1":     ("Color",    "Color"),
    "Gobo1":      ("Gobo",     "Gobo"),
    "Gobo2":      ("Gobo",     "Gobo"),
    "Gobo1Pos":   ("Gobo",     "Gobo"),
    "Gobo2Pos":   ("Gobo",     "Gobo"),
    "Focus":      ("Beam",     "Beam"),
    "Zoom":       ("Beam",     "Beam"),
    "Prism1":     ("Beam",     "Beam"),
    "ColorAdd_R": ("Color",    "Color"),
    "ColorAdd_G": ("Color",    "Color"),
    "ColorAdd_B": ("Color",    "Color"),
    "ColorAdd_W": ("Color",    "Color"),
    "ColorAdd_A": ("Color",    "Color"),
    "CTC":        ("Color",    "Color"),
    "CTO":        ("Color",    "Color"),
    "ColorSub_C": ("Color",    "Color"),
    "ColorSub_M": ("Color",    "Color"),
    "ColorSub_Y": ("Color",    "Color"),
    "XYZ_Z":      ("Position", "PanTilt"),
    "NoFeature":  ("Control",  "Control"),
}

# Physical unit per GDTF attribute
ATTR_PHYSICAL_UNIT = {
    "Pan":  "Angle",
    "Tilt": "Angle",
}

IDENTITY_MATRIX = "1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1"

# Physical ranges for known attributes
ATTR_PHYSICAL_RANGE = {
    "Pan":  ("-270", "270"),
    "Tilt": ("-135", "135"),
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _uuid() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


def _gdtf_filename(manufacturer: str, model: str, mode: str) -> str:
    """Build GDTF filename: {manufacturer}@{model}@{mode}.gdtf"""
    return f"{manufacturer}@{model}@{mode}.gdtf"


def _gdtf_attr(attr_type_id: int) -> str:
    """Map a ChamSys attr_type_id to a GDTF attribute name."""
    return ATTR_TYPE_MAP.get(attr_type_id, "NoFeature")


def _xml_declaration() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n'


def _indent_xml(root: ET.Element) -> None:
    """Indent XML tree for readability; no-op on Python < 3.9."""
    try:
        ET.indent(root)
    except AttributeError:
        pass  # Python < 3.9, skip indenting


def _tree_to_bytes(root: ET.Element) -> bytes:
    """Serialize an ElementTree root to UTF-8 bytes with XML declaration."""
    _indent_xml(root)
    raw = ET.tostring(root, encoding="unicode")
    return (_xml_declaration() + raw).encode("utf-8")


# ── GDTF Generation ─────────────────────────────────────────────────────────

def _build_channel_list(personality: dict) -> list:
    """Build the list of channels to emit in the GDTF, handling 16-bit pairing
    and multi-cell filtering.

    Returns a list of dicts:
        {
            'index': int,          # 0-based index in personality channels
            'name': str,
            'gdtf_attr': str,
            'is_16bit': bool,
            'fine_index': int|None, # index of the fine channel if 16-bit
            'offset_coarse': int,   # 1-based DMX offset (coarse)
            'offset_fine': int|None,# 1-based DMX offset (fine) if 16-bit
            'default': int|None,
        }
    """
    channels = personality.get("channels", [])
    chan_count = personality.get("chan_count", len(channels))
    defaults = personality.get("defaults", {})
    # Only consider channels within chan_count
    channels = channels[:chan_count]

    result = []
    skip_indices = set()

    for i, ch in enumerate(channels):
        if i in skip_indices:
            continue
        # Skip multi-cell channels
        if ch.get("is_multi_cell", False):
            continue
        # Skip fine channels (they'll be paired with their coarse channel)
        if ch.get("is_fine", False):
            continue

        gdtf_attr = _gdtf_attr(ch["attr_type_id"])

        # Check if next channel is the fine pair
        is_16bit = False
        fine_index = None
        if i + 1 < len(channels):
            next_ch = channels[i + 1]
            if (next_ch.get("is_fine", False)
                    and next_ch["attr_type_id"] == ch["attr_type_id"]
                    and not next_ch.get("is_multi_cell", False)):
                is_16bit = True
                fine_index = i + 1
                skip_indices.add(i + 1)

        # Get default value (use channel index as string key)
        default_val = defaults.get(str(i))
        if default_val is not None:
            max_val = 65535 if is_16bit else 255
            if default_val < 0 or default_val > max_val:
                default_val = None  # Skip invalid/corrupt values
            elif default_val == 0:
                default_val = None  # Omit default of 0

        entry = {
            "index": i,
            "name": ch["name"],
            "gdtf_attr": gdtf_attr,
            "attr_type_id": ch["attr_type_id"],
            "is_16bit": is_16bit,
            "fine_index": fine_index,
            "offset_coarse": i + 1,  # 1-based
            "offset_fine": (fine_index + 1) if fine_index is not None else None,
            "default": default_val,
        }
        result.append(entry)

    return result


def _build_gdtf_xml(personality: dict) -> ET.Element:
    """Build a GDTF description.xml ElementTree root for one personality."""
    manufacturer = personality.get("manufacturer", "Unknown")
    model = personality.get("model", "Unknown")
    mode = personality.get("mode", "Default")
    long_name = f"{manufacturer} {model} {mode}"

    root = ET.Element("GDTF", DataVersion="1.2")
    ft = ET.SubElement(root, "FixtureType",
                       Name=model,
                       ShortName=model,
                       LongName=long_name,
                       Manufacturer=manufacturer,
                       Description="",
                       FixtureTypeID=_uuid(),
                       Thumbnail="",
                       RefFT="")

    # Build channel list
    ch_list = _build_channel_list(personality)

    # Collect unique GDTF attributes used
    used_attrs = set()
    for ch in ch_list:
        used_attrs.add(ch["gdtf_attr"])

    # ── AttributeDefinitions ─────────────────────────────────────────────
    attr_defs = ET.SubElement(ft, "AttributeDefinitions")

    # Attributes
    attrs_el = ET.SubElement(attr_defs, "Attributes")
    for attr_name in sorted(used_attrs):
        fg, feat = ATTR_FEATURE_MAP.get(attr_name, ("Control", "Control"))
        phys_unit = ATTR_PHYSICAL_UNIT.get(attr_name, "None")
        ET.SubElement(attrs_el, "Attribute",
                      Name=attr_name,
                      Pretty=attr_name,
                      ActivationGroup="",
                      Feature=f"{fg}.{feat}",
                      PhysicalUnit=phys_unit)

    # FeatureGroups — emit the standard set
    fg_el = ET.SubElement(attr_defs, "FeatureGroups")
    standard_groups = [
        ("Dimmer",   "Dimmer",   "Dimmer"),
        ("Position", "Position", "PanTilt"),
        ("Color",    "Color",    "Color"),
        ("Gobo",     "Gobo",     "Gobo"),
        ("Beam",     "Beam",     "Beam"),
        ("Control",  "Control",  "Control"),
    ]
    for fg_name, fg_pretty, feat_name in standard_groups:
        fg_sub = ET.SubElement(fg_el, "FeatureGroup", Name=fg_name, Pretty=fg_pretty)
        ET.SubElement(fg_sub, "Feature", Name=feat_name)

    # ── Wheels ───────────────────────────────────────────────────────────
    wheels_el = ET.SubElement(ft, "Wheels")
    wheel_slots = personality.get("wheel_slots", {})
    channels = personality.get("channels", [])
    chan_count = personality.get("chan_count", len(channels))

    for slot_idx_str, slots in sorted(wheel_slots.items(), key=lambda x: int(x[0])):
        slot_idx = int(slot_idx_str)
        # Determine wheel name from channel name if available
        if slot_idx < chan_count and slot_idx < len(channels):
            wheel_name = channels[slot_idx].get("name", f"Wheel_{slot_idx}")
        else:
            wheel_name = f"Wheel_{slot_idx}"
        wheel_el = ET.SubElement(wheels_el, "Wheel", Name=wheel_name)
        for slot in slots:
            ET.SubElement(wheel_el, "Slot",
                          Name=slot.get("name", ""),
                          Color="")

    # ── PhysicalDescriptions, Models ─────────────────────────────────────
    ET.SubElement(ft, "PhysicalDescriptions")
    ET.SubElement(ft, "Models")

    # ── Geometries ───────────────────────────────────────────────────────
    geom_el = ET.SubElement(ft, "Geometries")
    body = ET.SubElement(geom_el, "Geometry",
                         Name="Body", Model="",
                         Position="{" + IDENTITY_MATRIX + "}")
    ET.SubElement(body, "Beam",
                  Name="Beam", Model="",
                  Position="{" + IDENTITY_MATRIX + "}",
                  LampType="Discharge",
                  PowerConsumption="300",
                  LuminousIntensity="10000",
                  BeamAngle="22",
                  FieldAngle="25",
                  BeamRadius="0.05",
                  BeamType="Wash",
                  ColorRenderingIndex="85")

    # ── DMXModes ─────────────────────────────────────────────────────────
    modes_el = ET.SubElement(ft, "DMXModes")
    dmx_mode = ET.SubElement(modes_el, "DMXMode", Name=mode, Geometry="Body")
    dmx_channels_el = ET.SubElement(dmx_mode, "DMXChannels")

    for ch in ch_list:
        attrs = {
            "DMXBreak": "1",
            "Geometry": "Body",
            "Highlight": "None",
        }

        if ch["is_16bit"]:
            attrs["Offset"] = f"{ch['offset_coarse']},{ch['offset_fine']}"
        else:
            attrs["Offset"] = str(ch["offset_coarse"])

        if ch["default"] is not None:
            attrs["Default"] = f"{ch['default']}/1"

        dmx_ch_el = ET.SubElement(dmx_channels_el, "DMXChannel", **attrs)

        # LogicalChannel
        logical = ET.SubElement(dmx_ch_el, "LogicalChannel",
                                Attribute=ch["gdtf_attr"],
                                Snap="No",
                                Master="None",
                                MibFade="0",
                                DMXChangeTimeLimit="0")

        # ChannelFunction
        phys_from, phys_to = ATTR_PHYSICAL_RANGE.get(ch["gdtf_attr"], ("0", "1"))
        resolution = "2" if ch["is_16bit"] else "1"
        ET.SubElement(logical, "ChannelFunction",
                      Attribute=ch["gdtf_attr"],
                      Name=ch["name"],
                      OriginalAttribute="",
                      DMXFrom=f"0/{resolution}",
                      DMXTo=f"255/{resolution}" if not ch["is_16bit"] else f"65535/{resolution}",
                      PhysicalFrom=phys_from,
                      PhysicalTo=phys_to,
                      RealFade="0",
                      RealAcceleration="0",
                      Wheel="",
                      Emitter="",
                      Filter="",
                      ModeMaster="",
                      ModeFrom=f"0/{resolution}",
                      ModeTo=f"0/{resolution}")

    return root


def _build_gdtf_zip(personality: dict) -> bytes:
    """Build a .gdtf ZIP archive (in memory) for one personality.
    Returns raw bytes of the ZIP."""
    root = _build_gdtf_xml(personality)
    xml_bytes = _tree_to_bytes(root)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("description.xml", xml_bytes)
    return buf.getvalue()


# ── MVR Scene XML Generation ────────────────────────────────────────────────

def _build_scene_xml(data: dict, personality_map: dict) -> ET.Element:
    """Build GeneralSceneDescription.xml root element.

    Args:
        data: the full JSON document
        personality_map: {personality_id: personality_dict}
    """
    root = ET.Element("GeneralSceneDescription")

    # UserData
    ud = ET.SubElement(root, "UserData")
    ET.SubElement(ud, "Data", provider="sour_patch", ver="1.0")

    # Scene
    scene = ET.SubElement(root, "Scene")

    # ── Layers ───────────────────────────────────────────────────────────
    layers = ET.SubElement(scene, "Layers")
    layer = ET.SubElement(layers, "Layer", name="Rig", uuid=_uuid())
    child_list = ET.SubElement(layer, "ChildList")

    heads = data.get("fixtures", {}).get("heads", [])
    # Sort by head_num
    sorted_heads = sorted(heads, key=lambda h: h.get("head_num", 0))

    # Build dmx_start → head map for group matching
    dmx_to_head_uuid = {}

    for head in sorted_heads:
        pid = head.get("personality_id")
        p = personality_map.get(pid)
        if p is None:
            print(f"Warning: head {head.get('head_num', '?')} references "
                  f"unknown personality_id {pid}, skipping",
                  file=sys.stderr)
            continue

        fixture_uuid = _uuid()
        model = p.get("model", "Unknown")
        head_num = head.get("head_num", 0)
        fixture_name = f"{model} {head_num}"

        gdtf_spec = _gdtf_filename(p.get("manufacturer", ""),
                                    model,
                                    p.get("mode", ""))
        gdtf_mode = p.get("mode", "")

        universe = head.get("universe", 1)
        dmx_start = head.get("dmx_start", 1)

        fix_el = ET.SubElement(child_list, "Fixture",
                               name=fixture_name,
                               uuid=fixture_uuid)

        matrix_el = ET.SubElement(fix_el, "Matrix")
        matrix_el.text = IDENTITY_MATRIX

        spec_el = ET.SubElement(fix_el, "GDTFSpec")
        spec_el.text = gdtf_spec

        mode_el = ET.SubElement(fix_el, "GDTFMode")
        mode_el.text = gdtf_mode

        addrs_el = ET.SubElement(fix_el, "Addresses")
        addr_el = ET.SubElement(addrs_el, "Address", **{"break": "0"})
        addr_el.text = f"{universe}.{dmx_start}"

        fid_el = ET.SubElement(fix_el, "FixtureID")
        fid_el.text = str(head_num)

        unit_el = ET.SubElement(fix_el, "UnitNumber")
        unit_el.text = "0"

        ftid_el = ET.SubElement(fix_el, "FixtureTypeId")
        ftid_el.text = "0"

        cid_el = ET.SubElement(fix_el, "CustomId")
        cid_el.text = str(head_num)

        # Track for group membership
        dmx_to_head_uuid[dmx_start] = fixture_uuid

    # ── GroupObjects ─────────────────────────────────────────────────────
    groups = data.get("programming", {}).get("groups", [])
    group_objects = ET.SubElement(scene, "GroupObjects")

    for g in groups:
        if not g.get("name"):
            continue
        go = ET.SubElement(group_objects, "GroupObject",
                           name=g["name"], uuid=_uuid())
        go_children = ET.SubElement(go, "ChildList")
        for addr in g.get("dmx_addresses", []):
            fixture_uuid = dmx_to_head_uuid.get(addr)
            if fixture_uuid is not None:
                # Reference the fixture by UUID
                ET.SubElement(go_children, "Fixture", uuid=fixture_uuid)

    return root


# ── Main export function ─────────────────────────────────────────────────────

def export_mvr(data: dict, output_path: str) -> dict:
    """Export the JSON data to an MVR ZIP file.

    Returns a summary dict with counts.
    """
    personalities = data.get("fixtures", {}).get("personalities", [])

    # Build personality lookup by id
    personality_map = {}
    for p in personalities:
        personality_map[p["id"]] = p

    # Build GDTF files
    gdtf_files = {}  # filename → bytes
    for p in personalities:
        fname = _gdtf_filename(p.get("manufacturer", ""),
                               p.get("model", ""),
                               p.get("mode", ""))
        gdtf_files[fname] = _build_gdtf_zip(p)

    # Build scene XML
    scene_root = _build_scene_xml(data, personality_map)
    scene_bytes = _tree_to_bytes(scene_root)

    # Count fixtures and groups for summary
    scene_xml_str = scene_bytes.decode("utf-8")
    fixture_count = scene_xml_str.count("<Fixture ")
    # Subtract fixtures inside GroupObject ChildLists (they're references)
    # Actually, count only fixtures within the Layer ChildList
    # The ones in GroupObjects are just references. Let's count properly.
    heads = data.get("fixtures", {}).get("heads", [])
    valid_heads = [h for h in heads if h.get("personality_id") in personality_map]
    fixture_count = len(valid_heads)

    groups = data.get("programming", {}).get("groups", [])
    group_count = sum(1 for g in groups if g.get("name"))

    # Write MVR ZIP
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as mvr:
        mvr.writestr("GeneralSceneDescription.xml", scene_bytes)
        for fname, gdtf_bytes in sorted(gdtf_files.items()):
            mvr.writestr(fname, gdtf_bytes)

    return {
        "gdtf_count": len(gdtf_files),
        "fixture_count": fixture_count,
        "group_count": group_count,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Export a JSON intermediate file to MVR (My Virtual Rig)."
    )
    ap.add_argument("json", help="Path to .json file produced by shw_json.py")
    ap.add_argument("--out", help="Output .mvr path (default: <name>.mvr beside input)")
    args = ap.parse_args()

    if not os.path.isfile(args.json):
        sys.exit(f"Error: file not found: {args.json}")

    out_path = args.out or os.path.splitext(args.json)[0] + ".mvr"

    with open(args.json, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    summary = export_mvr(data, out_path)

    basename = os.path.basename(out_path)
    print(f"Wrote {basename}")
    print(f"  {summary['gdtf_count']} GDTF fixture types")
    print(f"  {summary['fixture_count']} fixtures in scene")
    print(f"  {summary['group_count']} groups")


if __name__ == "__main__":
    main()
