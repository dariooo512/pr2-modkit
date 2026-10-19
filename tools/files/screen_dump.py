#!/usr/bin/env python3
"""screen_dump.py - read, print, edit and write Port Royale 2 UI layout files (*.screen).

A .screen file is an A2dLib (A2dLib-2.20A.dll) "A2DFILE" version 14 stream of drawables
(controls). The format is summarised in the kit's docs/techniques/01-game-files.md section 4;
every field offset, with the A2dLib parser VAs as evidence, is in Sovereign Nations'
research/screen_format.md (https://github.com/dariooo512/pr2-sovereign-nations).

Usage (the default folder is PR2_WORKDIR/arcd/scripts, the extracted arcd archive's screens)
  python screen_dump.py dump FILE.screen [--all]      tree of controls (--all: every field)
  python screen_dump.py verify [DIR_OR_FILES...]      parse->write and parse->json->write must
                                                      give identical bytes for every .screen
  python screen_dump.py json FILE.screen OUT.json     export an editable JSON tree
  python screen_dump.py build IN.json OUT.screen      write a .screen from (edited) JSON
  python screen_dump.py grep REGEX [DIR]              find controls whose name / class /
                                                      anim / font / text id matches REGEX

Python API (for generator scripts)
  from screen_dump import Screen, Node, res_hash
  scr = Screen.load(path)            # scr.root is the top CScreen node
  n = scr.find("CHE_TmplHeadBg")     # depth-first search by control name
  n.get("x"); n.set("w", 300)        # common fields: x y w h flags tooltip colorop ...
  n.set("anim", r"scripts\\MyIcon.anim")            # class fields (see SPECS below)
  n.set_text_id("text", "IDT_MYMOD_TITLE")          # sets the id name AND its hash
  c = n.clone("MYMOD_NewName")       # deep copy (children renamed with rename_children())
  parent.children.append(c)
  scr.save(out_path)

The writer starts from the raw bytes of each block and patches only the fields you change,
so untouched controls are reproduced byte-for-byte (including padding garbage).
"""
from __future__ import annotations

import base64
import copy
import glob
import json
import os
import re
import struct
import sys

HEADER_SIZE = 0x1C
FILE_VERSION = 14            # A2dLib CVersionedFile version written by the editor (0x10125E7C)
READ_VERSIONS = (13, 14)     # 224 vanilla screens are v14, 30 are v13 (same block layout)
NONE = "-none-"              # A2dLib RESNAME_NONE: "no resource"

DWFAB_SIZE = 0x1A0
DWB_SIZE = 0x130
SOUND_SIZE = 0x218
SCRN_SIZE = 0x238
LISTPOINT_SIZE = 8

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kit_paths import WORKDIR  # noqa: E402  (setting PR2_WORKDIR)

DEFAULT_SCRIPTS = str(WORKDIR / "arcd" / "scripts")


# --------------------------------------------------------------------------- hashing
def res_hash(name: str) -> int:
    """A2dLib string-id hash (same function as the .res string tables).
    Verified on every text id stored in the 254 vanilla screens (7,000+ pairs)."""
    if name in ("", "ID_NONE"):
        return -1
    h = 0
    for ch in name.encode("latin-1"):
        c = ch - 256 if ch >= 128 else ch
        m = (h * 0x71 + c) & 0xFFFFFFFF
        m = m - (1 << 32) if m >= 1 << 31 else m
        h = abs(m) % 999999991 * (1 if m >= 0 else -1)
    return h


# --------------------------------------------------------------------------- field specs
# (name, offset, type, note).  types: i32 u32 color tag8 str<N>  ("hash" = i32 text-id hash)
# Confidence of every name is given in screen_format.md ([S] read from the loader, [H] guess).
F = lambda name, off, typ, note="": (name, off, typ, note)

SPEC_DWFAB = [
    F("tag", 0x000, "tag8", "'SH_DWFAB'"),
    F("size", 0x008, "u32", "0x1A0"),
    F("fab_id", 0x00C, "i32", "always -1"),
    F("class", 0x010, "str64", "A2dLib class: CScreen CStatic CButton CTextBox ..."),
    F("name", 0x050, "str256", "control name (PR2 code finds controls by this)"),
    F("fab_res1", 0x150, "i32", "always -1"),
    F("fab_res2", 0x154, "i32", "always -1"),
    F("ext_class", 0x158, "str64", "PR2 subclass tried first (CIconButton, CScrollObject...)"),
]
SPEC_DWB = [
    F("tag", 0x000, "tag8", "'SH_DWB'"),
    F("size", 0x008, "u32", "0x130"),
    F("x", 0x00C, "i32", "position relative to parent"),
    F("y", 0x010, "i32"),
    F("w", 0x014, "i32"),
    F("h", 0x018, "i32"),
    F("flags", 0x01C, "u32", "bit0 enabled, bit1 visible"),
    F("tooltip_hash", 0x020, "hash"),
    F("tooltip", 0x024, "str256", "tooltip string id"),
    F("sound_count", 0x124, "u32", "number of 0x218-byte sound records after this block"),
    F("colorop", 0x128, "i32", "Walhalla R2D_COLOROPS (7 = normal)"),
    F("unused_12c", 0x12C, "i32", "not read by the v14 loader"),
]
SPEC_SCRN = [
    F("tag", 0x000, "tag8", "'SH_SCRN'"),
    F("size", 0x008, "u32", "0x238"),
]
SPEC = {
    "CScreen": SPEC_SCRN,
    "CStatic": [
        F("size", 0x000, "u32", "0x10C"),
        F("anim", 0x004, "str264", "scripts\\*.anim, or -none-"),
    ],
    "CStaticImg": [
        F("size", 0x000, "u32", "0x11C"),
        F("image", 0x004, "str264", "images\\*.aim"),
        F("img_h", 0x10C, "i32", "[H]"),
        F("img_w", 0x110, "i32", "[H]"),
    ],
    "CTextBox": [
        F("size", 0x000, "u32", "0x258"),
        F("font", 0x004, "str256", "fonts\\*.res or -none-"),
        F("text", 0x104, "str256", "string id (ID_NONE = set by code)"),
        F("text_hash", 0x204, "hash"),
        F("unused_208", 0x208, "i32"),
        F("text_dx", 0x20C, "i32", "text offset x"),
        F("text_dy", 0x210, "i32", "text offset y"),
        F("color", 0x214, "color", "text colour ARGB"),
        F("effect_color", 0x218, "color", "outline/shadow colour"),
        F("bg_color", 0x21C, "color", "fill colour (alpha 0 = none)"),
        F("effect", 0x220, "i32", "0 none, 1 outline, 2 shadow +d,+d, 3 shadow -d,-d"),
        F("effect_size", 0x224, "i32", "outline/shadow distance px"),
        F("halign", 0x228, "i32", "0 left, 1 right, 2 centre, 3 justify"),
        F("valign", 0x22C, "i32", "0 centre, 1 top, 2 bottom"),
        F("border_color_top", 0x230, "color"),
        F("border_color_right", 0x234, "color"),
        F("border_color_bottom", 0x238, "color"),
        F("border_color_left", 0x23C, "color"),
        F("border_top", 0x240, "i32", "border width px"),
        F("border_right", 0x244, "i32"),
        F("border_bottom", 0x248, "i32"),
        F("border_left", 0x24C, "i32"),
    ],
    "CTextBoxEx": [
        F("size", 0x000, "u32", "0x254"),
        F("font", 0x004, "str256"),
        F("text", 0x104, "str256", "string id; rich text, word-wrapped"),
        F("text_hash", 0x204, "hash"),
        F("color", 0x208, "color", "[H] default text colour"),
        F("effect_color", 0x20C, "color", "[H]"),
        F("bg_color", 0x210, "color", "[H]"),
        F("effect", 0x214, "i32", "[H] as CTextBox"),
        F("effect_size", 0x218, "i32", "[H]"),
        F("margin_x", 0x21C, "i32", "[H]"),
        F("margin_y", 0x220, "i32", "[H] / line spacing"),
        F("align", 0x224, "i32", "[H] 0 left, 1 right, 2 centre"),
        F("flag_228", 0x228, "i32", "[H]"),
        F("border_color_top", 0x22C, "color"),
        F("border_color_right", 0x230, "color"),
        F("border_color_bottom", 0x234, "color"),
        F("border_color_left", 0x238, "color"),
        F("border_top", 0x23C, "i32"),
        F("border_right", 0x240, "i32"),
        F("border_bottom", 0x244, "i32"),
        F("border_left", 0x248, "i32"),
    ],
    "CButton": [
        F("size", 0x000, "u32", "0xC60"),
        F("unused_004", 0x004, "i32"),
        F("text_dx", 0x008, "i32"),
        F("text_dy", 0x00C, "i32"),
        F("text_hash_dis", 0x010, "hash"),
        F("text_hash_nr", 0x014, "hash"),
        F("text_hash_pr", 0x018, "hash"),
        F("text_hash_hl", 0x01C, "hash"),
        F("text_dis", 0x020, "str256", "label string id per state"),
        F("text_nr", 0x120, "str256"),
        F("text_pr", 0x220, "str256"),
        F("text_hl", 0x320, "str256"),
        F("color_dis", 0x420, "color", "label colour per state"),
        F("color_nr", 0x424, "color"),
        F("color_pr", 0x428, "color"),
        F("color_hl", 0x42C, "color"),
        F("color_4", 0x430, "color", "5th colour (often uninitialised)"),
        F("effect_color", 0x434, "color"),
        F("font", 0x438, "str256"),
        F("anim_dis", 0x538, "str256", "disabled"),
        F("anim_nr", 0x638, "str256", "normal"),
        F("anim_pr", 0x738, "str256", "pressed"),
        F("anim_hl", 0x838, "str256", "highlight (hover)"),
        F("anim_4", 0x938, "str256", "[H] extra state"),
        F("anim_5", 0xA38, "str256", "[H] extra state"),
        F("anim_checked", 0xB38, "str256", "check-mark overlay (checkboxes)"),
        F("mode_c38", 0xC38, "i32", "member +0x9D4, OnDraw tests ==1 [H]"),
        F("halign", 0xC3C, "i32", "[H]"),
        F("valign", 0xC40, "i32", "[H]"),
        F("effect", 0xC44, "i32", "label effect (as CTextBox)"),
        F("effect_size", 0xC48, "i32", "signed byte used"),
        F("toggle", 0xC4C, "i32", "1 = toggle/check button [H]"),
        F("unused_c50", 0xC50, "i32"),
        F("press_dx", 0xC54, "i32", "label shift while pressed"),
        F("press_dy", 0xC58, "i32"),
        F("unused_c5c", 0xC5C, "i32"),
    ],
    "CSlider": [
        F("size", 0x000, "u32", "0x220"),
        F("value_004", 0x004, "i32", "[H]"),
        F("min", 0x008, "i32", "[H]"),
        F("max", 0x00C, "i32", "[H]"),
        F("pos", 0x010, "i32", "[H]"),
        F("page", 0x014, "i32", "[H]"),
        F("str_018", 0x018, "str256", "[H] resource"),
        F("str_118", 0x118, "str256", "[H] resource"),
        F("step_small", 0x218, "i32", "[H]"),
        F("step_large", 0x21C, "i32", "[H]"),
    ],
    "CEditBox": [
        F("size", 0x000, "u32", "0x368"),
        F("text_dx", 0x008, "i32", "[H]"),
        F("text_dy", 0x00C, "i32", "[H]"),
        F("font", 0x010, "str256"),
        F("color", 0x110, "color", "[H]"),
        F("color_114", 0x114, "color", "[H]"),
        F("color_118", 0x118, "color", "[H]"),
        F("color_11c", 0x11C, "color", "[H]"),
        F("color_120", 0x120, "color", "[H]"),
        F("max_len", 0x12C, "i32", "[H]"),
        F("mode_130", 0x130, "i32", "[H]"),
        F("anim", 0x138, "str256", "[H]"),
        F("time_238", 0x238, "i32", "[H] ms"),
        F("time_23c", 0x23C, "i32", "[H] ms"),
        F("time_240", 0x240, "i32", "[H] ms"),
        F("val_244", 0x244, "i32", "[H]"),
        F("val_248", 0x248, "i32", "[H]"),
        F("val_250", 0x250, "i32", "[H]"),
        F("val_258", 0x258, "i32", "[H]"),
        F("val_25c", 0x25C, "i32", "[H]"),
        F("val_260", 0x260, "i32", "[H] not a hash"),
        F("text", 0x264, "str256", "[H] string id"),
    ],
    "CProgressBar": [
        F("size", 0x000, "u32", "0x218"),
        F("max", 0x00C, "i32", "[H]"),
        F("anim_bg", 0x010, "str256", "[H]"),
        F("anim_bar", 0x110, "str256", "[H]"),
    ],
    "CVideoSKS": [
        F("size", 0x000, "u32", "0x110"),
        F("flags_104", 0x104, "i32", "[H]"),
    ],
    "CListBox": [
        F("size", 0x000, "u32", "0x40"),
        F("cols", 0x008, "i32", "[H] grid columns"),
        F("rows", 0x00C, "i32", "[H] grid rows"),
        F("cols2", 0x014, "i32", "[H]"),
        F("rows2", 0x018, "i32", "[H]"),
        F("flags", 0x028, "i32", "[H] member +0x118"),
        F("entry_count", 0x030, "u32", "(col,row)+drawable records that follow"),
        F("template_count", 0x034, "u32", "(col,row)+template drawables that follow"),
    ],
}
CLASS_BLOCK_SIZE = {
    "CScreen": SCRN_SIZE, "CStatic": 0x10C, "CStaticImg": 0x11C, "CTextBox": 0x258,
    "CTextBoxEx": 0x254, "CButton": 0xC60, "CSlider": 0x220, "CEditBox": 0x368,
    "CProgressBar": 0x218, "CVideoSKS": 0x110, "CListBox": 0x40, "CDrawable": 0,
}
SPEC_INDEX = {"DWFAB": {f[0]: f for f in SPEC_DWFAB}, "DWB": {f[0]: f for f in SPEC_DWB}}
for _k, _v in SPEC.items():
    SPEC_INDEX[_k] = {f[0]: f for f in _v}


def _fsize(typ):
    if typ in ("i32", "u32", "color", "hash"):
        return 4
    if typ == "tag8":
        return 8
    return int(typ[3:])


def read_field(buf, off, typ):
    if typ in ("i32", "hash"):
        return struct.unpack_from("<i", buf, off)[0]
    if typ in ("u32", "color"):
        return struct.unpack_from("<I", buf, off)[0]
    raw = bytes(buf[off:off + _fsize(typ)])
    return raw.split(b"\0", 1)[0].decode("latin-1")


def write_field(buf, off, typ, val):
    if typ in ("i32", "hash"):
        struct.pack_into("<i", buf, off, int(val))
    elif typ in ("u32", "color"):
        if isinstance(val, str):
            val = int(val.lstrip("#"), 16)
        struct.pack_into("<I", buf, off, int(val) & 0xFFFFFFFF)
    else:
        n = _fsize(typ)
        b = val.encode("latin-1")
        if len(b) >= n:
            raise ValueError("string too long for field (%d >= %d): %r" % (len(b), n, val))
        buf[off:off + n] = b + b"\0" * (n - len(b))


def fmt_value(typ, v):
    if typ == "color":
        return "#%08X" % v
    if typ in ("i32", "u32", "hash"):
        return str(v)
    return repr(v)


# --------------------------------------------------------------------------- tree
class Node:
    """One drawable: DWFAB + DWB (+ sound records) + class block (+ children)."""

    def __init__(self, fab: bytearray, dwb: bytearray, sounds, props: bytearray):
        self.fab = fab
        self.dwb = dwb
        self.sounds = sounds          # list of 0x218-byte bytearrays (none in vanilla files)
        self.props = props            # class block (bytearray, may be empty)
        self.children: list[Node] = []        # CScreen children / CSlider 4 elements /
                                              # CListBox: [vertical slider, horizontal slider]
        self.lb_entries: list[tuple[int, int, Node]] = []    # CListBox only
        self.lb_templates: list[tuple[int, int, Node]] = []  # CListBox only

    # -- identity
    @property
    def cls(self):
        return read_field(self.fab, 0x10, "str64")

    @property
    def name(self):
        return read_field(self.fab, 0x50, "str256")

    @property
    def ext_class(self):
        return read_field(self.fab, 0x158, "str64")

    # -- generic field access ("x", "anim", "font", "text" ...)
    def _locate(self, field):
        for blk, spec in (("dwb", "DWB"), ("props", self.cls), ("fab", "DWFAB")):
            idx = SPEC_INDEX.get(spec, {})
            if field in idx:
                return getattr(self, blk), idx[field]
        raise KeyError("%s has no field %r" % (self.cls, field))

    def get(self, field):
        buf, (_, off, typ, _) = self._locate(field)
        return read_field(buf, off, typ)

    def set(self, field, value):
        buf, (_, off, typ, _) = self._locate(field)
        write_field(buf, off, typ, value)
        return self

    def has(self, field):
        try:
            self._locate(field)
            return True
        except KeyError:
            return False

    def set_text_id(self, field, string_id):
        """Set a string-id field and its hash ('text' -> 'text_hash', 'tooltip' ->
        'tooltip_hash', CButton 'text_nr' -> 'text_hash_nr')."""
        self.set(field, string_id)
        hf = {"tooltip": "tooltip_hash", "text": "text_hash"}.get(field)
        if hf is None and field.startswith("text_"):
            hf = "text_hash_" + field[5:]
        if hf and self.has(hf):
            self.set(hf, res_hash(string_id))
        return self

    def set_rect(self, x, y, w, h):
        return self.set("x", x).set("y", y).set("w", w).set("h", h)

    def rename(self, new_name):
        write_field(self.fab, 0x50, "str256", new_name)
        return self

    def clone(self, new_name=None):
        c = copy.deepcopy(self)
        if new_name:
            c.rename(new_name)
        return c

    def rename_children(self, old_prefix, new_prefix):
        """Recursively replace a name prefix (e.g. 'CHE_' -> 'FNC_')."""
        for n in self.walk():
            if n.name.startswith(old_prefix):
                n.rename(new_prefix + n.name[len(old_prefix):])
        return self

    def walk(self):
        yield self
        for _, _, t in self.lb_entries:
            yield from t.walk()
        for _, _, t in self.lb_templates:
            yield from t.walk()
        for c in self.children:
            yield from c.walk()

    def find(self, name):
        for n in self.walk():
            if n.name == name:
                return n
        return None

    def remove(self, name):
        for i, c in enumerate(self.children):
            if c.name == name:
                return self.children.pop(i)
        for c in self.children:
            r = c.remove(name)
            if r is not None:
                return r
        return None


# --------------------------------------------------------------------------- reader
class FormatError(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.o = 0
        self.version = FILE_VERSION

    def take(self, n):
        if self.o + n > len(self.d):
            raise FormatError("unexpected end of file at 0x%X (need %d bytes)" % (self.o, n))
        b = bytearray(self.d[self.o:self.o + n])
        self.o += n
        return b

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def peek(self, n):
        return self.d[self.o:self.o + n]


def _read_node(r: _Reader) -> Node:
    at = r.o
    if r.peek(8) != b"SH_DWFAB":
        raise FormatError("expected SH_DWFAB at 0x%X, got %r" % (at, r.peek(8)))
    fab = r.take(DWFAB_SIZE)
    if struct.unpack_from("<I", fab, 8)[0] != DWFAB_SIZE:
        raise FormatError("SH_DWFAB size != 0x1A0 at 0x%X" % at)
    if r.peek(6) != b"SH_DWB":
        raise FormatError("expected SH_DWB at 0x%X" % r.o)
    dwb = r.take(DWB_SIZE)
    nsnd = struct.unpack_from("<I", dwb, 0x124)[0]
    # sound records: 0x218 bytes in v14, 0x110 in v13 (A2dLib 0x1001D8FD / 0x1001D58B);
    # no vanilla screen has any.
    sounds = [r.take(SOUND_SIZE if r.version >= 14 else 0x110) for _ in range(nsnd)]
    cls = read_field(fab, 0x10, "str64")
    if cls not in CLASS_BLOCK_SIZE:
        raise FormatError("unknown drawable class %r at 0x%X" % (cls, at))
    want = CLASS_BLOCK_SIZE[cls]
    props = r.take(want) if want else bytearray()
    if want and cls != "CScreen" and struct.unpack_from("<I", props, 0)[0] != want:
        raise FormatError("%s block size mismatch at 0x%X" % (cls, at))
    node = Node(fab, dwb, sounds, props)
    if cls == "CScreen":
        for _ in range(r.u32()):
            node.children.append(_read_node(r))
    elif cls == "CSlider":           # background, increase, decrease, drag button
        for _ in range(4):
            node.children.append(_read_node(r))
    elif cls == "CListBox":
        ne = struct.unpack_from("<I", props, 0x30)[0]
        nt = struct.unpack_from("<I", props, 0x34)[0]
        for lst, cnt in ((node.lb_entries, ne), (node.lb_templates, nt)):
            for _ in range(cnt):
                col, row = struct.unpack("<ii", r.take(LISTPOINT_SIZE))
                lst.append((col, row, _read_node(r)))
        node.children = [_read_node(r), _read_node(r)]   # vertical, horizontal slider
    return node


def _write_node(n: Node, out: bytearray):
    struct.pack_into("<I", n.dwb, 0x124, len(n.sounds))
    out += n.fab
    out += n.dwb
    for s in n.sounds:
        out += s
    out += n.props
    cls = n.cls
    if cls == "CScreen":
        out += struct.pack("<I", len(n.children))
        for c in n.children:
            _write_node(c, out)
    elif cls == "CSlider":
        if len(n.children) != 4:
            raise FormatError("CSlider %r needs exactly 4 element drawables" % n.name)
        for c in n.children:
            _write_node(c, out)
    elif cls == "CListBox":
        struct.pack_into("<I", n.props, 0x30, len(n.lb_entries))
        struct.pack_into("<I", n.props, 0x34, len(n.lb_templates))
        for lst in (n.lb_entries, n.lb_templates):
            for col, row, t in lst:
                out += struct.pack("<ii", col, row)
                _write_node(t, out)
        if len(n.children) != 2:
            raise FormatError("CListBox %r needs 2 slider drawables" % n.name)
        for c in n.children:
            _write_node(c, out)
    elif n.children:
        raise FormatError("%s %r cannot have children in a .screen" % (cls, n.name))


class Screen:
    def __init__(self, header: bytes, root: Node, path=None):
        self.header = bytearray(header)
        self.root = root
        self.path = path

    @classmethod
    def parse(cls, data: bytes, path=None):
        if data[:8] != b"A2DFILE\0":
            raise FormatError("not an A2DFILE")
        hs, ver = struct.unpack_from("<II", data, 8)
        if hs != HEADER_SIZE or ver not in READ_VERSIONS:
            raise FormatError("unsupported header size/version %d/%d" % (hs, ver))
        r = _Reader(data)
        r.version = ver
        hdr = r.take(HEADER_SIZE)
        root = _read_node(r)
        if r.o != len(data):
            raise FormatError("%d trailing bytes after the root drawable" % (len(data) - r.o))
        return cls(hdr, root, path)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls.parse(f.read(), path)

    def to_bytes(self) -> bytes:
        out = bytearray(self.header)
        _write_node(self.root, out)
        return bytes(out)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    def find(self, name):
        return self.root.find(name)

    def walk(self):
        return self.root.walk()


def new_screen(name, w, h, x=0, y=0, template=None):
    """A fresh CScreen root, built from a vanilla screen's root bytes (template path
    defaults to INTERFACE_TAB_WINDOW_800x600.screen) with no children."""
    tpl = template or os.path.join(DEFAULT_SCRIPTS, "INTERFACE_TAB_WINDOW_800x600.screen")
    s = Screen.load(tpl)
    root = s.root
    root.children = []
    root.rename(name)
    root.set_rect(x, y, w, h)
    return Screen(s.header, root)


# --------------------------------------------------------------------------- JSON
def _fields(buf, spec):
    return {name: read_field(buf, off, typ) for name, off, typ, _ in spec}


HASH_PAIRS = {"tooltip": "tooltip_hash", "text": "text_hash", "text_dis": "text_hash_dis",
              "text_nr": "text_hash_nr", "text_pr": "text_hash_pr", "text_hl": "text_hash_hl"}


def _apply(buf, spec, values):
    """Patch only the fields whose value differs from the raw bytes. A changed string id
    whose hash was left as it was gets its hash recomputed."""
    idx = {f[0]: f for f in spec}
    changed = set()
    for name, off, typ, _ in spec:
        if name in values and values[name] != read_field(buf, off, typ):
            write_field(buf, off, typ, values[name])
            changed.add(name)
    for idf, hf in HASH_PAIRS.items():
        if idf in changed and hf in idx and hf not in changed:
            write_field(buf, idx[hf][1], "hash", res_hash(values[idf]))


def node_to_json(n: Node):
    j = {
        "class": n.cls, "name": n.name, "ext_class": n.ext_class,
        "fab": _fields(n.fab, SPEC_DWFAB),
        "dwb": _fields(n.dwb, SPEC_DWB),
        "props": _fields(n.props, SPEC.get(n.cls, [])) if n.props else {},
        "raw": {"fab": base64.b64encode(bytes(n.fab)).decode(),
                "dwb": base64.b64encode(bytes(n.dwb)).decode(),
                "props": base64.b64encode(bytes(n.props)).decode(),
                "sounds": [base64.b64encode(bytes(s)).decode() for s in n.sounds]},
        "children": [node_to_json(c) for c in n.children],
    }
    if n.cls == "CListBox":
        j["lb_entries"] = [[c, r_, node_to_json(t)] for c, r_, t in n.lb_entries]
        j["lb_templates"] = [[c, r_, node_to_json(t)] for c, r_, t in n.lb_templates]
    return j


def node_from_json(j) -> Node:
    raw = j.get("raw", {})
    fab = bytearray(base64.b64decode(raw["fab"]))
    dwb = bytearray(base64.b64decode(raw["dwb"]))
    props = bytearray(base64.b64decode(raw.get("props", "")))
    sounds = [bytearray(base64.b64decode(s)) for s in raw.get("sounds", [])]
    _apply(fab, SPEC_DWFAB, j.get("fab", {}))
    if j.get("name") is not None and j["name"] != read_field(fab, 0x50, "str256"):
        write_field(fab, 0x50, "str256", j["name"])
    _apply(dwb, SPEC_DWB, j.get("dwb", {}))
    cls = read_field(fab, 0x10, "str64")
    _apply(props, SPEC.get(cls, []), j.get("props", {}))
    n = Node(fab, dwb, sounds, props)
    n.children = [node_from_json(c) for c in j.get("children", [])]
    n.lb_entries = [(c, r_, node_from_json(t)) for c, r_, t in j.get("lb_entries", [])]
    n.lb_templates = [(c, r_, node_from_json(t)) for c, r_, t in j.get("lb_templates", [])]
    return n


def screen_to_json(s: Screen):
    return {"format": "pr2-screen-v14", "header": base64.b64encode(bytes(s.header)).decode(),
            "root": node_to_json(s.root)}


def screen_from_json(j) -> Screen:
    return Screen(base64.b64decode(j["header"]), node_from_json(j["root"]))


# --------------------------------------------------------------------------- printing
def _flags(n):
    f = n.get("flags")
    s = ("" if f & 2 else " HIDDEN") + ("" if f & 1 else " DISABLED")
    return s + ("" if f in (0, 1, 2, 3) else " flags=0x%X" % f)


def summary(n: Node):
    cls = n.cls
    ext = "<%s>" % n.ext_class if n.ext_class else ""
    s = '%s%s "%s" @(%d,%d) %dx%d%s' % (cls, ext, n.name, n.get("x"), n.get("y"),
                                        n.get("w"), n.get("h"), _flags(n))
    tt = n.get("tooltip")
    if tt and tt != "ID_NONE":
        s += " tip=" + tt
    if n.get("colorop") != 7:
        s += " colorop=%d" % n.get("colorop")
    p = []
    if cls == "CStatic":
        p.append("anim=" + n.get("anim"))
    elif cls == "CStaticImg":
        p.append("image=" + n.get("image"))
    elif cls in ("CTextBox", "CTextBoxEx"):
        p.append("font=" + os.path.basename(n.get("font")))
        p.append("text=" + n.get("text"))
        p.append("color=#%08X" % n.get("color"))
        if cls == "CTextBox":
            p.append("align=%s/%s" % ({0: "L", 1: "R", 2: "C", 3: "J"}.get(n.get("halign"), "?"),
                                      {0: "C", 1: "T", 2: "B"}.get(n.get("valign"), "?")))
        if n.get("effect"):
            p.append("effect=%d/%d #%08X" % (n.get("effect"), n.get("effect_size"),
                                             n.get("effect_color")))
        if n.get("bg_color") >> 24:
            p.append("bg=#%08X" % n.get("bg_color"))
    elif cls == "CButton":
        anims = [n.get(k) for k in ("anim_nr", "anim_hl", "anim_pr", "anim_dis")]
        p.append("anims(nr/hl/pr/dis)=" + "|".join(os.path.basename(a) for a in anims))
        if n.get("anim_checked") != NONE:
            p.append("checked=" + os.path.basename(n.get("anim_checked")))
        t = n.get("text_nr")
        if t != "ID_NONE":
            p.append("text=" + t)
        if n.get("font") != NONE:
            p.append("font=" + os.path.basename(n.get("font")))
    elif cls == "CEditBox":
        p.append("font=" + os.path.basename(n.get("font")))
    elif cls == "CListBox":
        p.append("entries=%d templates=%d" % (len(n.lb_entries), len(n.lb_templates)))
    elif cls == "CProgressBar":
        p.append("bar=" + n.get("anim_bar"))
    return s + ("  " + " ".join(p) if p else "")


def dump(n: Node, depth=0, full=False, out=None, prefix=""):
    out = out if out is not None else []
    ind = "  " * depth
    out.append(ind + prefix + summary(n))
    if full:
        for blk, spec in ((n.dwb, SPEC_DWB), (n.props, SPEC.get(n.cls, []))):
            for name, off, typ, note in spec:
                if name in ("tag", "size"):
                    continue
                out.append("%s    .%-20s %s%s" % (ind, name, fmt_value(typ, read_field(blk, off, typ)),
                                                  ("   # " + note) if note else ""))
    for c, r_, t in n.lb_entries:
        dump(t, depth + 1, full, out, "[entry %d,%d] " % (c, r_))
    for c, r_, t in n.lb_templates:
        dump(t, depth + 1, full, out, "[template %d,%d] " % (c, r_))
    if n.cls == "CSlider":
        for role, c in zip(("[bg] ", "[inc] ", "[dec] ", "[drag] "), n.children):
            dump(c, depth + 1, full, out, role)
    elif n.cls == "CListBox":
        for role, c in zip(("[vslider] ", "[hslider] "), n.children):
            dump(c, depth + 1, full, out, role)
    else:
        for c in n.children:
            dump(c, depth + 1, full, out)
    return out


# --------------------------------------------------------------------------- CLI
def _screen_files(args):
    files = []
    for a in args or [DEFAULT_SCRIPTS]:
        if os.path.isdir(a):
            files += sorted(glob.glob(os.path.join(a, "*.screen")))
        else:
            files += sorted(glob.glob(a))
    return files


def cmd_verify(args):
    files = _screen_files(args)
    ok = fail = 0
    nodes = 0
    for f in files:
        data = open(f, "rb").read()
        try:
            s = Screen.parse(data, f)
            b1 = s.to_bytes()
            b2 = screen_from_json(json.loads(json.dumps(screen_to_json(s)))).to_bytes()
            if b1 == data and b2 == data:
                ok += 1
                nodes += sum(1 for _ in s.walk())
            else:
                fail += 1
                print("MISMATCH", os.path.basename(f), "direct" if b1 != data else "json")
        except FormatError as e:
            fail += 1
            print("FAIL", os.path.basename(f), e)
    print("round-trip byte-identical: %d / %d files (%d controls); failures: %d"
          % (ok, len(files), nodes, fail))
    return 0 if fail == 0 and ok else 1


def cmd_grep(args):
    pat = re.compile(args[0], re.I)
    for f in _screen_files(args[1:]):
        s = Screen.load(f)
        for n in s.walk():
            hay = [n.name, n.cls, n.ext_class, n.get("tooltip")]
            for k in ("anim", "image", "font", "text", "anim_nr", "anim_hl", "text_nr"):
                if n.has(k):
                    hay.append(str(n.get(k)))
            if any(pat.search(h or "") for h in hay):
                print("%s: %s" % (os.path.basename(f), summary(n)))


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[1], argv[2:]
    if cmd == "dump":
        full = "--all" in args
        for f in [a for a in args if not a.startswith("--")]:
            s = Screen.load(f)
            print("# %s  (%d bytes)" % (f, len(s.to_bytes())))
            print("\n".join(dump(s.root, full=full)))
        return 0
    if cmd == "verify":
        return cmd_verify(args)
    if cmd == "json":
        s = Screen.load(args[0])
        with open(args[1], "w", encoding="utf-8") as f:
            json.dump(screen_to_json(s), f, indent=1)
        return 0
    if cmd == "build":
        with open(args[0], encoding="utf-8") as f:
            s = screen_from_json(json.load(f))
        s.save(args[1])
        Screen.load(args[1])           # re-parse as a self-check
        print("wrote", args[1])
        return 0
    if cmd == "grep":
        cmd_grep(args)
        return 0
    print("unknown command", cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
