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


def out_dir(root=None) -> Path:
    """Where a browse of ``root`` belongs, resolved per invocation.

    `out/browse/` is a per-base tree, so the answer depends on which install
    is being browsed and cannot be a module constant: one resolved at import
    names whichever install was configured *then*, and `--root <other client>`
    would write its pages into the configured client's namespace. Same shape
    as C21, and the same fix `artcrawl.out_dir_for` already uses. C55.
    """
    return coroot.derived_path("out/browse", root)


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
    out = out_dir(args.root)
    out.mkdir(parents=True, exist_ok=True)
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
        dest = out / f"textures_{name}.html"
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
    out = out_dir(args.root)
    out.mkdir(parents=True, exist_ok=True)
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
    if not rows:
        # No loose .DMap: this client ships them inside .7z. Report the
        # REGISTRY rather than an empty page -- "0 maps" for a client holding
        # 730 of them is the failure this fallback exists for.
        reg = read_gamemap(root)
        if reg:
            rows = ["<tr data-k='%s'><td>%s<td colspan=4>%s<td>%s</tr>"
                    % (html.escape(pp.lower()), mid, html.escape(pp), tile)
                    for mid, pp, tile in reg]
            print("  no loose .DMap files; read ini/GameMap.dat instead "
                  "(%d registered maps)" % len(reg))
    body = ("<table><tr><th>file<th>ver<th>size<th>walkable<th>layers<th>puzzle</tr>"
            + "".join(rows) + "</table>")
    dest = out / "maps.html"
    dest.write_text(_page("CO map index", f"{len(rows)} maps in map/map/", body), "utf-8")
    print(f"wrote {dest}  ({len(rows)} maps)")
    return 0


def read_gamemap(root) -> list:
    """`ini/GameMap.dat` as [(id, archive_path, tile)] -- it is NOT encrypted.

    7878 has ZERO loose `.DMap` files: every map ships inside a `.7z` under
    `map/map/`, so `cmd_maps`'s glob found nothing and reported an empty index
    for a client carrying 730 maps.

    Layout, verified by EXACT CONSUMPTION rather than by eye: a u32 count, then
    `count` records of (u32 id, u32 len, len bytes of path, u32 tile). On 7878
    that consumes 24,413 of 24,413 bytes and the declared count equals the
    record count. A parse off by one field ends mid-file, so the two checks
    together are the guard and either alone is not.
    """
    import struct
    f = Path(root) / "ini" / "GameMap.dat"
    if not f.exists():
        return []
    b = f.read_bytes()
    if len(b) < 4:
        return []
    declared = struct.unpack_from("<I", b, 0)[0]
    off, out = 4, []
    while off + 8 <= len(b):
        mid, ln = struct.unpack_from("<II", b, off)
        off += 8
        if ln > 512 or off + ln > len(b):
            break
        path = b[off:off + ln].decode("latin-1")
        off += ln
        tile = struct.unpack_from("<I", b, off)[0] if off + 4 <= len(b) else 0
        off += 4
        out.append((mid, path, tile))
    if off != len(b) or declared != len(out):
        # Say so rather than returning a plausible short list. A registry that
        # parsed 80% of the way reads as "this client has fewer maps", which is
        # the wrong conclusion arrived at confidently.
        print("  GameMap.dat: PARTIAL parse -- %d records, declared %d, "
              "consumed %d of %d bytes. Not trusted."
              % (len(out), declared, off, len(b)))
        return []
    return out


def cmd_data(args) -> int:
    """A searchable page over the DECRYPTED `.dat` corpus.

    The asset browser answers "what does this look like". Nothing answered
    "what is in the game data". This does, over the tables whose joins are
    established in `docs/dat_corpus_index_2026-08-27.md`.
    """
    out = out_dir(args.root)
    out.mkdir(parents=True, exist_ok=True)
    corpus = Path(args.corpus)
    if not corpus.is_dir():
        sys.exit("no decrypted corpus at %s -- pass --corpus" % corpus)

    def rows_of(name):
        f = corpus / (name + ".dat.out")
        if not f.exists():
            return []
        acc = []
        for ln in f.read_text(encoding="latin-1").split(chr(10)):
            ln = ln.strip()
            if not ln or ln.startswith(";"):
                continue
            x = ln.split("@@")
            if x and x[-1] == "":
                x = x[:-1]
            acc.append(x)
        return acc

    items = {int(x[0]): x[1] for x in rows_of("itemtype") if x and x[0].isdigit()}
    if not items:
        # Every lookup below would return "" and every table would render with
        # blank names -- a page that looks built and says nothing.
        sys.exit("itemtype parsed to ZERO rows; refusing to build a page whose "
                 "every lookup would silently miss")

    def nm(v):
        try:
            return items.get(int(v), "")
        except (TypeError, ValueError):
            return ""

    sections, counts = [], {}

    inst = rows_of("instancetype")
    if inst:
        r = ["<tr data-k='%s'><td>%s<td>%s</tr>"
             % (html.escape((x[1] + " " + x[0]).lower()),
                html.escape(x[0]), html.escape(x[1].replace("~", " ")))
             for x in inst if len(x) > 1]
        counts["instances"] = len(r)
        sections.append("<h2>Instances</h2><table><tr><th>id<th>name</tr>"
                        + "".join(r) + "</table>")

    ex = rows_of("exchange_shop_goods")
    if ex:
        r = []
        for x in ex:
            if len(x) < 7:
                continue
            got, cur = nm(x[1]), nm(x[5])
            if not got:
                continue
            r.append("<tr data-k='%s'><td>%s<td>%s<td>%s</tr>"
                     % (html.escape((got + " " + cur).lower()),
                        html.escape(got), html.escape(x[6]), html.escape(cur)))
        counts["exchange offers"] = len(r)
        sections.append("<h2>Exchange shop</h2><table>"
                        "<tr><th>reward<th>price<th>paid in</tr>"
                        + "".join(r) + "</table>")

    tr = rows_of("task_reward_type")
    if tr:
        r = []
        for x in tr:
            if len(x) < 30:
                continue
            tot = sum(int(x[c]) for c in range(22, 30)
                      if x[c].lstrip("-").isdigit())
            if not tot:
                continue
            for c in range(14, 22):
                w = int(x[c + 8]) if x[c + 8].lstrip("-").isdigit() else 0
                if not w:
                    continue
                # An id that does not resolve is SHOWN, not dropped. Skipping
                # it made 4 of 220 tables render totals of 84%, 10% and 0% with
                # nothing on the page saying why -- a reader would read those
                # as the game's own drop rates. Now every table sums to 100%
                # and an unresolved entry is visibly unresolved.
                n = nm(x[c]) or ("&lt;unresolved id %s&gt;" % html.escape(x[c]))
                # Normalise by the ROW's own total, never by a constant: two
                # rows in this table sum to 100,000 and the rest to 10,000.
                r.append("<tr data-k='%s'><td>%s<td>%s<td>%.2f%%</tr>"
                         % (html.escape((n + " " + x[0]).lower()),
                            html.escape(x[0]), n, 100.0 * w / tot))
        counts["drop entries"] = len(r)
        sections.append("<h2>Task rewards (drop tables)</h2><table>"
                        "<tr><th>table<th>item<th>chance</tr>"
                        + "".join(r) + "</table>")

    maps = read_gamemap(args.root)
    if maps:
        r = ["<tr data-k='%s'><td>%s<td>%s<td>%s</tr>"
             % (html.escape(pth.lower()), mid, html.escape(pth), tile)
             for mid, pth, tile in maps]
        counts["maps"] = len(r)
        sections.append("<h2>Maps</h2><table><tr><th>id<th>archive<th>tile</tr>"
                        + "".join(r) + "</table>")

    # Two renderings, because one string cannot serve both surfaces: the page
    # wants an HTML entity and the console wants a character. Printing the
    # HTML form leaked a literal "&middot;" into the operator's terminal.
    parts = ["%s %s" % ("{:,}".format(v), k) for k, v in counts.items()]
    sub_html = " &middot; ".join(parts)
    sub_text = " | ".join(parts)
    dest = out / "data.html"
    dest.write_text(_page("CO game data", sub_html or "nothing parsed",
                          "".join(sections)), "utf-8")
    # Every line is either prose or a command, and never both. The first
    # version printed "  open it:  start ..." -- the owner pasted the whole
    # block into PowerShell and got two parse errors, because an indented line
    # that CONTAINS a command reads as a command. Prose is prefixed with #.
    print("# wrote %s" % dest)
    print("# %s" % (sub_text or "nothing parsed"))
    print("# to open it, run the line below")
    print('start "" "%s"' % dest)
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

    p = sub.add_parser("data",
                       help="searchable page over the decrypted .dat corpus")
    p.add_argument("--corpus",
                   default=str(coroot.assets_dir() / "derived"
                               / "7878-dat-decrypted"),
                   help="directory of decrypted *.dat.out files")
    p.set_defaults(func=cmd_data)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
