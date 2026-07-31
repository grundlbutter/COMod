#!/usr/bin/env python3
"""
inidb.py -- loader and schema profiler for $ROOT/ini/, the client-side game
database.

The directory mixes four unrelated shapes.  This module identifies each one and
gives you a uniform way to load it:

  json_list     21 files  a JSON array of flat objects -- the real game tables
                          (itemtype, magictype, monster, npc, shop, ...)
  json_dict      4 files  a JSON object used as a map
  ini_sectioned 27 files  classic "[Section]" + "key=value" (3DEffect, armor,
                          weapon, armet, 3dmotion, Mount, ...)
  ini_flat_kv    8 files  "key=value" with no sections; the key is often a
                          compound id like "1.999.130" (ActionSound, StrRes)
  ini_records            whitespace-delimited positional rows (Font.ini,
                          graphic.ini) and the binary .TME/.dat blobs, which are
                          reported but not parsed

ENCODING (verified): `codepage.ini` contains the single byte '0'.  Every .json
file decodes as strict UTF-8.  No .ini file in this build contains a byte above
0x7F, so the codepage never actually comes into play here -- but the loader
still honours it (0 -> cp1252 fallback, 936 -> gbk, 950 -> big5) because the
original TQ clients shipped GBK data and a future patch could reintroduce it.

Usage:
    python tools/inidb.py schemas -o out/ini/schemas.json
    python tools/inidb.py show itemtype.json --limit 3
    python tools/inidb.py show armor.ini --limit 3

As a module:
    from inidb import load, load_all, classify
    rows = load(root / "ini" / "itemtype.json")
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import coroot                            # noqa: E402

ROOT_DEFAULT = coroot.default_root()

CODEPAGE_FALLBACK = {0: "cp1252", 936: "gbk", 950: "big5", 65001: "utf-8"}

# Files that are binary blobs rather than text config.
BINARY_SUFFIXES = {".tme", ".dat"}


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------

def read_codepage(root: Path) -> int:
    p = root / "ini" / "codepage.ini"
    try:
        return int(p.read_text(encoding="ascii", errors="ignore").strip() or 0)
    except (OSError, ValueError):
        return 0


def decode_text(raw: bytes, codepage: int = 0) -> tuple[str, str]:
    """Decode config text.  Returns (text, encoding_used)."""
    for enc in ("utf-8-sig", CODEPAGE_FALLBACK.get(codepage, "cp1252"), "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace"), "latin-1/replace"


# ---------------------------------------------------------------------------
# classification and loading
# ---------------------------------------------------------------------------

def classify(path: Path, raw: bytes | None = None) -> str:
    if raw is None:
        raw = path.read_bytes()
    if path.suffix.lower() == ".json":
        try:
            d = json.loads(raw.decode("utf-8-sig"))
        except Exception:  # noqa: BLE001
            return "json_invalid"
        return "json_list" if isinstance(d, list) else "json_dict"
    if path.suffix.lower() in BINARY_SUFFIXES:
        return "binary"
    # a config file with a high proportion of bytes outside printable+CRLF is
    # really a blob that happens to have a .ini name
    printable = sum(1 for c in raw if 32 <= c < 127 or c in (9, 10, 13))
    if raw and printable / len(raw) < 0.85:
        return "binary"
    txt, _ = decode_text(raw)
    lines = [l for l in txt.splitlines() if l.strip() and not l.lstrip().startswith(";")]
    if not lines:
        return "empty"
    if any(l.lstrip().startswith("[") for l in lines):
        return "ini_sectioned"
    if sum(1 for l in lines if "=" in l) > len(lines) * 0.5:
        return "ini_flat_kv"
    return "ini_records"


def parse_sectioned(txt: str) -> "OrderedDict[str, OrderedDict[str, str]]":
    """[Section] / key=value.  Duplicate keys keep the LAST value, matching the
    behaviour of GetPrivateProfileString."""
    out: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()
    cur: OrderedDict[str, str] | None = None
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith(";") or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = out.setdefault(s[1:-1], OrderedDict())
            continue
        if "=" in s and cur is not None:
            k, v = s.split("=", 1)
            cur[k.strip()] = v.strip()
    return out


def parse_flat_kv(txt: str) -> "OrderedDict[str, str]":
    out: OrderedDict[str, str] = OrderedDict()
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith(";") or s.startswith("#"):
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_records(txt: str) -> list[list[str]]:
    rows = []
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith(";") or s.startswith("#"):
            continue
        rows.append(s.split())
    return rows


def load(path: Path, codepage: int = 0):
    """Load one ini/ file into native Python data."""
    raw = path.read_bytes()
    kind = classify(path, raw)
    if kind in ("json_list", "json_dict"):
        return json.loads(raw.decode("utf-8-sig"))
    if kind == "binary":
        return raw
    txt, _ = decode_text(raw, codepage)
    if kind == "ini_sectioned":
        return parse_sectioned(txt)
    if kind == "ini_flat_kv":
        return parse_flat_kv(txt)
    if kind == "ini_records":
        return parse_records(txt)
    return None


def load_all(root: Path) -> dict[str, object]:
    cp = read_codepage(root)
    out = {}
    for p in sorted((root / "ini").rglob("*")):
        if p.is_file():
            out[p.relative_to(root / "ini").as_posix()] = load(p, cp)
    return out


# ---------------------------------------------------------------------------
# schema profiling
# ---------------------------------------------------------------------------

_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d*\.\d+$")


def _scalar_type(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if v is None:
        return "null"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    s = str(v)
    if _INT.match(s):
        return "int-string"
    if _FLOAT.match(s):
        return "float-string"
    return "string"


def profile_columns(rows, max_enum: int = 24) -> dict:
    """Field-by-field profile of a list of flat dicts."""
    cols: dict[str, dict] = OrderedDict()
    n = len(rows)
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            c = cols.setdefault(k, {"present": 0, "types": Counter(),
                                    "values": set(), "min": None, "max": None,
                                    "maxlen": 0, "truncated": False})
            c["present"] += 1
            c["types"][_scalar_type(v)] += 1
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                c["min"] = v if c["min"] is None else min(c["min"], v)
                c["max"] = v if c["max"] is None else max(c["max"], v)
            if isinstance(v, str):
                c["maxlen"] = max(c["maxlen"], len(v))
            if len(c["values"]) <= max_enum:
                try:
                    c["values"].add(v if isinstance(v, (str, int, float, bool, type(None)))
                                    else json.dumps(v)[:60])
                except TypeError:
                    c["truncated"] = True
            else:
                c["truncated"] = True

    out = OrderedDict()
    for k, c in cols.items():
        d = {
            "present_in_rows": c["present"],
            "present_pct": round(100.0 * c["present"] / n, 2) if n else 0,
            "types": dict(c["types"]),
        }
        if c["min"] is not None:
            d["min"], d["max"] = c["min"], c["max"]
        if c["maxlen"]:
            d["max_string_len"] = c["maxlen"]
        if not c["truncated"] and len(c["values"]) <= max_enum:
            d["distinct_values"] = sorted(c["values"], key=lambda x: (x is None, str(x)))
            d["looks_like_enum"] = len(c["values"]) <= 12
        else:
            d["distinct_values"] = f">{max_enum} distinct"
        out[k] = d
    return out


def profile_file(path: Path, codepage: int = 0) -> dict:
    raw = path.read_bytes()
    kind = classify(path, raw)
    rec: dict = {"kind": kind, "bytes": len(raw)}
    try:
        data = load(path, codepage)
    except Exception as ex:  # noqa: BLE001
        rec["error"] = str(ex)
        return rec

    if kind == "json_list":
        rec["rows"] = len(data)
        if data and all(isinstance(r, dict) for r in data):
            rec["columns"] = profile_columns(data)
            rec["column_count"] = len(rec["columns"])
        elif data:
            rec["element_types"] = dict(Counter(_scalar_type(x) for x in data))
        rec["sample"] = data[:2]
    elif kind == "json_dict":
        rec["keys"] = len(data)
        rec["sample_keys"] = list(data)[:8]
        vals = list(data.values())
        rec["value_types"] = dict(Counter(_scalar_type(v) for v in vals))
        if vals and all(isinstance(v, dict) for v in vals):
            rec["columns"] = profile_columns(vals)
    elif kind == "ini_sectioned":
        rec["sections"] = len(data)
        rec["sample_sections"] = list(data)[:8]
        keysets = Counter()
        allkeys = Counter()
        for sec in data.values():
            keysets[tuple(sorted(sec))] += 1
            for k in sec:
                allkeys[k] += 1
        rec["distinct_key_layouts"] = len(keysets)
        rec["keys_by_frequency"] = allkeys.most_common(40)
        rec["columns"] = profile_columns(list(data.values()))
    elif kind == "ini_flat_kv":
        rec["entries"] = len(data)
        rec["sample"] = dict(list(data.items())[:6])
        rec["key_shape"] = dict(Counter(
            re.sub(r"\d+", "#", k) for k in data).most_common(8))
    elif kind == "ini_records":
        rec["rows"] = len(data)
        rec["field_counts"] = dict(Counter(len(r) for r in data).most_common(8))
        rec["sample"] = data[:4]
    elif kind == "binary":
        rec["note"] = "binary blob, not text config"
        rec["first_bytes_hex"] = raw[:16].hex()
    return rec


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_schemas(a) -> int:
    cp = read_codepage(a.root)
    ini = a.root / "ini"
    files = sorted(p for p in ini.rglob("*") if p.is_file())
    out = {"_meta": {"root": str(ini), "codepage_ini": cp,
                     "codepage_fallback": CODEPAGE_FALLBACK.get(cp, "cp1252"),
                     "file_count": len(files)},
           "files": {}}
    kinds = Counter()
    for p in files:
        rel = p.relative_to(ini).as_posix()
        try:
            rec = profile_file(p, cp)
        except Exception as ex:  # noqa: BLE001
            rec = {"error": str(ex)}
        kinds[rec.get("kind", "?")] += 1
        out["files"][rel] = rec
    out["_meta"]["kind_counts"] = dict(kinds)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out["_meta"], indent=2))
    return 0


def cmd_show(a) -> int:
    p = a.root / "ini" / a.name
    if not p.exists():
        p = Path(a.name)
    cp = read_codepage(a.root)
    print(json.dumps(profile_file(p, cp), indent=2, default=str)[:a.limit * 2000])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ini/ database loader + profiler")
    ap.add_argument("--root", type=Path, default=ROOT_DEFAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("schemas")
    p.add_argument("-o", "--out", type=Path,
                   default=Path(__file__).resolve().parents[1]
                   / "out" / "ini" / "schemas.json")
    p.set_defaults(func=cmd_schemas)

    p = sub.add_parser("show")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=cmd_show)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
