#!/usr/bin/env python
"""One palette for a whole floor, at 16 and 64 colours.

    uv run --with pillow scripts/floor_palette.py

No API calls: this re-quantizes the images already in scratch/img/.

A palette chosen per image gives each room its best possible sixteen colours, but no two
rooms then share a look. A floor palette is chosen once from all the rooms together and
every room is mapped onto it, so the floor reads as one place — the same trade a real
16-bit game made, because the hardware held one palette at a time.

Each model's five rooms are treated as one floor, so the palettes are derived per model
and the comparison stays fair.

Columns per room:
  PER-IMAGE 16   sixteen colours chosen for this room alone — the reference
  FLOOR 16       sixteen colours shared by all five rooms
  FLOOR 64       sixty-four shared

Everything is Floyd-Steinberg dithered, and per-image contrast stretch is applied in all
three, so palette choice is the only thing that differs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.scene import HEIGHT, WIDTH

ROOT = Path(__file__).resolve().parent.parent
IMGS = ROOT / "scratch" / "img"
PAGE = ROOT / "scratch" / "floor_palette.html"

ROOM_ORDER = ["Hewn Passage", "The Sump", "Lantern Ledge", "The Fall", "The Sorting Room"]


def load(path: Path):
    from PIL import Image, ImageOps
    img = Image.open(path).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    return ImageOps.autocontrast(img, cutoff=1)


def palette_over(images, colors: int):
    """Derive one palette from every room at once by stacking them into one picture."""
    from PIL import Image
    strip = Image.new("RGB", (WIDTH, HEIGHT * len(images)))
    for i, im in enumerate(images):
        strip.paste(im, (0, i * HEIGHT))
    return strip.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors)


def locked(img, pal_img, colors: int) -> dict:
    """Map one room onto a palette chosen elsewhere."""
    from PIL import Image
    flat = pal_img.getpalette()[: colors * 3]
    ref = Image.new("P", (1, 1))
    ref.putpalette(flat * (256 // colors))       # pad so unused slots cannot be picked
    return encode(img.quantize(palette=ref, dither=Image.Dither.FLOYDSTEINBERG), colors)


def adaptive(img, colors: int) -> dict:
    from PIL import Image
    return encode(img.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors,
                              dither=Image.Dither.FLOYDSTEINBERG), colors)


def encode(img_p, size: int) -> dict:
    pal = img_p.getpalette()[: size * 3]
    colours = ["#%02x%02x%02x" % tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
    data = list(img_p.getdata())
    rows = ["".join(f"{data[y * WIDTH + x] % size:02x}" for x in range(WIDTH))
            for y in range(HEIGHT)]
    return {"palette": colours, "rows": rows,
            "used": len({v % size for v in data})}


def main() -> None:
    files = sorted(IMGS.glob("*.png"))
    if not files:
        raise SystemExit(f"no images in {IMGS} — run palette_bakeoff.py first")

    by_model: dict[str, dict[str, Path]] = {}
    for f in files:
        room_slug, model = f.stem.split("--")
        by_model.setdefault(model, {})[room_slug] = f

    out = {"width": WIDTH, "height": HEIGHT, "models": []}
    for model, rooms in sorted(by_model.items()):
        order = [r for r in ROOM_ORDER if r.lower().replace(" ", "-") in rooms]
        images = [load(rooms[r.lower().replace(" ", "-")]) for r in order]
        pal16, pal64 = palette_over(images, 16), palette_over(images, 64)
        entry = {"model": model,
                 "floor16": ["#%02x%02x%02x" % tuple(pal16.getpalette()[i:i + 3])
                             for i in range(0, 48, 3)],
                 "floor64": ["#%02x%02x%02x" % tuple(pal64.getpalette()[i:i + 3])
                             for i in range(0, 192, 3)],
                 "rooms": []}
        for name, img in zip(order, images):
            entry["rooms"].append({
                "room": name,
                "own16": adaptive(img, 16),
                "floor16": locked(img, pal16, 16),
                "floor64": locked(img, pal64, 64),
            })
        out["models"].append(entry)

    PAGE.write_text(TEMPLATE.replace("/*__DATA__*/null", json.dumps(out)))
    print(PAGE)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Floor Palette Test</title>
<style>
  :root{--ink:#120d18;--panel:#1a1422;--line:#31283c;--text:#deeed6;--dim:#8595a1;
        --gold:#dad45e;
        --mono:ui-monospace,"IBM Plex Mono",Menlo,monospace;
        --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
  *{box-sizing:border-box}
  body{margin:0;padding:32px 24px 64px;background:var(--ink);color:var(--text);
       font:15px/1.6 var(--sans)}
  .wrap{max-width:1180px;margin:0 auto}
  h1{font:700 13px/1 var(--mono);letter-spacing:.32em;text-transform:uppercase;
     color:var(--gold);margin:0 0 10px}
  .lede{color:var(--dim);max-width:74ch;margin:0 0 26px}
  .model{border-top:1px solid var(--line);padding-top:24px;margin-bottom:38px}
  .model h2{font:700 14px/1.3 var(--mono);color:var(--gold);margin:0 0 12px}
  .pals{display:flex;flex-wrap:wrap;gap:22px;margin-bottom:22px}
  .pal b{display:block;font:700 10px/1.4 var(--mono);letter-spacing:.16em;
         text-transform:uppercase;color:var(--dim);margin-bottom:5px}
  .strip{display:flex;flex-wrap:wrap;max-width:520px;border:1px solid var(--line)}
  .strip i{width:16px;height:16px;display:block}
  .room{margin-bottom:20px}
  .room h3{font:11px/1 var(--mono);letter-spacing:.16em;margin:0 0 10px;
           padding-bottom:8px;border-bottom:1px dashed var(--line)}
  .cards{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
  .who{font:11px/1.4 var(--mono);color:var(--dim);margin-bottom:6px}
  canvas{width:100%;display:block;image-rendering:pixelated;background:#000;
         border:1px solid var(--line)}
  .meta{font:11px/1.6 var(--mono);color:var(--dim);margin-top:5px}
</style></head><body><div class="wrap">
<h1>Floor Palette Test</h1>
<p class="lede">The same fifteen images as before, no regeneration. Each model's five rooms
are treated as one floor: a single palette is derived from all five together, then every
room is mapped onto it. The per-image palette is kept alongside as the reference.</p>
<div id="out"></div></div>
<script>
const D = /*__DATA__*/null;
const COLS=[["own16","Per-image 16 — reference"],["floor16","Floor 16 — locked"],
            ["floor64","Floor 64 — locked"]];

function draw(v){
  const cv=document.createElement('canvas');
  cv.width=D.width; cv.height=D.height;
  const ctx=cv.getContext('2d'), im=ctx.createImageData(D.width,D.height);
  for(let y=0;y<D.height;y++){
    const row=v.rows[y];
    for(let x=0;x<D.width;x++){
      const hex=v.palette[parseInt(row.substr(x*2,2),16)]||'#000', o=(y*D.width+x)*4;
      im.data[o]=parseInt(hex.slice(1,3),16); im.data[o+1]=parseInt(hex.slice(3,5),16);
      im.data[o+2]=parseInt(hex.slice(5,7),16); im.data[o+3]=255;
    }
  }
  ctx.putImageData(im,0,0); return cv;
}
function strip(cols){
  return `<div class="strip">${cols.map(c=>`<i style="background:${c}"></i>`).join('')}</div>`;
}

const out=document.getElementById('out');
for(const m of D.models){
  const sec=document.createElement('section'); sec.className='model';
  sec.innerHTML=`<h2>${m.model}</h2><div class="pals">`+
    `<div class="pal"><b>Floor palette · 16</b>${strip(m.floor16)}</div>`+
    `<div class="pal"><b>Floor palette · 64</b>${strip(m.floor64)}</div></div>`;
  for(const r of m.rooms){
    const blk=document.createElement('div'); blk.className='room';
    const h3=document.createElement('h3'); h3.textContent=r.room; blk.appendChild(h3);
    const cards=document.createElement('div'); cards.className='cards';
    for(const [k,label] of COLS){
      const c=document.createElement('div');
      const w=document.createElement('div'); w.className='who'; w.textContent=label;
      c.append(w, draw(r[k]));
      const meta=document.createElement('div'); meta.className='meta';
      meta.textContent=`${r[k].used} colours used`;
      c.appendChild(meta); cards.appendChild(c);
    }
    blk.appendChild(cards); sec.appendChild(blk);
  }
  out.appendChild(sec);
}
</script></body></html>
"""


if __name__ == "__main__":
    main()
