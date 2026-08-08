#!/usr/bin/env python3
"""
cobrowse.py -- visual asset browser for Classic Conquer 2.0.

Builds a self-contained HTML page of texture thumbnails so you can *see* what
an appearance ID actually looks like before editing it. Every tile carries the
logical asset path and a ready-to-paste comod.py command.

    py -3 tools/cobrowse.py textures --table body --limit 400
    py -3 tools/cobrowse.py textures --dir c3/weapon --limit 300
    py -3 tools/cobrowse.py maps

Output goes to out/browse/*.html. The page embeds its own PNGs as data URIs, so
it works offline and can be moved anywhere.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                                 # noqa: E402
from coassets import DEFAULT_ROOT, AssetRoot, DMap, dds_info  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
OUT = coroot.derived_path("out/browse")


def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        sys.exit("Pillow is required (py -3 -m pip install Pillow)")


CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#111; --mut:#666; --line:#e3e3e3; --card:#fafafa; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151517; --fg:#e8e8ea; --mut:#9a9aa2; --line:#2c2c30; --card:#1d1d20; }
}
* { box-sizing:border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
       font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:var(--mut); margin-bottom:20px; }
#q { width:100%; max-width:420px; padding:8px 11px; border:1px solid var(--line);
     border-radius:7px; background:var(--card); color:var(--fg); margin-bottom:20px; font-size:14px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:14px; }
.card { border:1px solid var(--line); border-radius:9px; padding:9px; background:var(--card); }
.card img { width:100%; height:auto; image-rendering:pixelated; border-radius:5px;
            background:repeating-conic-gradient(#0002 0% 25%, transparent 0% 50%) 50%/14px 14px; }
.id { font-weight:600; margin-top:7px; font-size:13px; word-break:break-all; }
.meta { color:var(--mut); font-size:11px; margin-top:2px; word-break:break-all; }
code { font:11px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;
       display:block; margin-top:6px; padding:5px 6px; background:#8881; border-radius:4px;
       white-space:pre-wrap; word-break:break-all; cursor:pointer; }
.hidden { display:none; }
table { border-collapse:collapse; width:100%; }
th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); font-size:13px; }
th { color:var(--mut); font-weight:600; }
"""

JS = """
const q=document.getElementById('q');
if(q){q.addEventListener('input',()=>{
  const v=q.value.toLowerCase();
  document.querySelectorAll('[data-k]').forEach(el=>{
    el.classList.toggle('hidden', v && !el.dataset.k.includes(v));
  });
});}
document.querySelectorAll('code').forEach(c=>c.addEventListener('click',()=>{
  navigator.clipboard.writeText(c.textContent);
  const o=c.textContent; c.textContent='copied'; setTimeout(()=>c.textContent=o,700);
}));
"""


def _thumb(data: bytes, size: int) -> str | None:
    Image = _pil()
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGBA")
    except Exception:
        return None
    im.thumbnail((size, size), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _page(title: str, subtitle: str, body: str, search: bool = True) -> str:
    q = '<input id="q" placeholder="filter...">' if search else ""
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style><h1>{html.escape(title)}</h1>"
            f"<div class=sub>{subtitle}</div>{q}{body}<script>{JS}</script>")


def cmd_textures(args) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with AssetRoot(args.root) as R:
        targets: list[tuple[str, str]] = []   # (label, logical)

        if args.table:
            tables = R.part_tables()
            ini = tables.get(args.table)
            if not ini:
                print(f"unknown table {args.table!r}; available: {', '.join(sorted(tables))}")
                return 1
            for app in ini:
                for pr in app.parts:
                    loc = R.resolve_asset(pr.texture, "texture")
                    if loc:
                        targets.append((f"{app.ident}", loc.logical))
                        break
            label = f"appearance table: {args.table} ({ini.name})"
        else:
            d = args.dir.strip("/")
            seen = set()
            for logical in _list_dir(R, d):
                if logical not in seen:
                    seen.add(logical)
                    targets.append((Path(logical).stem, logical))
            label = f"directory: {d}"

        targets = targets[:args.limit]
        cards = []
        skipped = 0
        for name, logical in targets:
            try:
                data = R.read(logical)
            except Exception:
                skipped += 1
                continue
            info = dds_info(data)
            b64 = _thumb(data, args.size)
            if not b64:
                skipped += 1
                continue
            key = f"{name} {logical}".lower()
            cmd = f"py -3 tools/comod.py extract {logical} --png"
            cards.append(
                f'<div class=card data-k="{html.escape(key)}">'
                f'<img src="data:image/png;base64,{b64}" alt="">'
                f'<div class=id>{html.escape(name)}</div>'
                f'<div class=meta>{html.escape(str(info) if info else "?")}</div>'
                f'<div class=meta>{html.escape(logical)}</div>'
                f'<code>{html.escape(cmd)}</code></div>')

        sub = (f"{len(cards)} textures &middot; {label} &middot; "
               f"click a command to copy it")
        if skipped:
            sub += f" &middot; {skipped} skipped (unreadable)"
        page = _page("CO texture browser", sub, f"<div class=grid>{''.join(cards)}</div>")
        name = args.table or args.dir.replace("/", "_")
        dest = OUT / f"textures_{name}.html"
        dest.write_text(page, "utf-8")
        print(f"wrote {dest}  ({len(cards)} thumbnails)")
    return 0


def _list_dir(R: AssetRoot, d: str) -> list[str]:
    """Every asset under a logical directory: loose files plus recovered
    archive names."""
    out = []
    p = R.root / d
    if p.is_dir():
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() == ".dds":
                out.append(f"{d}/{f.relative_to(p).as_posix()}")
    R.name_for(0)  # force name table load
    for nm in (R._names or {}).values():
        if nm.startswith(d + "/") and nm.endswith(".dds"):
            out.append(nm)
    return sorted(set(out))


def cmd_maps(args) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    root = Path(args.root)
    rows = []
    for p in sorted((root / "map" / "map").glob("*.[Dd][Mm]ap")):
        try:
            m = DMap.load(p)
        except Exception as e:
            rows.append(f"<tr data-k='{html.escape(p.name.lower())}'><td>{html.escape(p.name)}</td>"
                        f"<td colspan=5>parse failed: {html.escape(str(e)[:60])}</td></tr>")
            continue
        walk = m.walkable_mask()
        open_cells = sum(sum(1 for c in row if c) for row in walk)
        pct = 100 * open_cells // max(m.width * m.height, 1)
        rows.append(
            f"<tr data-k='{html.escape((p.name + ' ' + m.puzzle_path).lower())}'>"
            f"<td>{html.escape(p.name)}</td><td>{m.version}</td>"
            f"<td>{m.width}&times;{m.height}</td><td>{pct}%</td>"
            f"<td>{m.layer_count}</td><td>{html.escape(m.puzzle_path)}</td></tr>")
    body = ("<table><tr><th>file<th>ver<th>size<th>walkable<th>layers<th>puzzle</tr>"
            + "".join(rows) + "</table>")
    dest = OUT / "maps.html"
    dest.write_text(_page("CO map index", f"{len(rows)} maps in map/map/", body), "utf-8")
    print(f"wrote {dest}  ({len(rows)} maps)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("textures", help="contact sheet of textures")
    p.add_argument("--table", help="appearance table (body, l_weapon, armet, ...)")
    p.add_argument("--dir", default="c3/texture", help="logical directory instead")
    p.add_argument("--limit", type=int, default=400)
    p.add_argument("--size", type=int, default=128)
    p.set_defaults(func=cmd_textures)

    p = sub.add_parser("maps", help="index of all world maps")
    p.set_defaults(func=cmd_maps)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
