#!/usr/bin/env python
"""First-person rooms, dithered, at four palette sizes.

    uv run --with pillow scripts/palette_bakeoff.py
    uv run --with pillow scripts/palette_bakeoff.py --page   # rebuild from saved images

The image model draws at full colour; code shrinks to 64x48 and quantizes. Since the
quantizing is ours, palette size is a free choice — every variant below comes from the
same generated image, so comparing them costs nothing extra.

  DB16      the fixed 16-colour palette, now with dithering — the old result, fairly
            treated, so the dithering alone can be judged
  16 / 64 / 256   a palette chosen per image from the colours actually in it

Dithering is Floyd-Steinberg throughout: the error from each pixel is pushed into its
neighbours, so two palette colours alternate where the palette cannot reach the true
one and the eye reads a third.

Source PNGs are kept in scratch/img/ so re-running the comparison never re-pays for them.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings
from app.models.scene import HEIGHT, WIDTH

# DawnBringer 16, kept here rather than in the app: the app now picks a palette per
# room, and this script exists to show why that beat a fixed one.
DB16 = [(0x14,0x0c,0x1c),(0x44,0x24,0x34),(0x30,0x34,0x6d),(0x4e,0x4a,0x4e),
        (0x85,0x4c,0x30),(0x34,0x65,0x24),(0xd0,0x46,0x48),(0x75,0x71,0x61),
        (0x59,0x7d,0xce),(0xd2,0x7d,0x2c),(0x85,0x95,0xa1),(0x6d,0xaa,0x2c),
        (0xd2,0xaa,0x99),(0x6d,0xc2,0xca),(0xda,0xd4,0x5e),(0xde,0xee,0xd6)]

MODELS = [
    "google/gemini-3.1-flash-lite-image",
    "google/gemini-2.5-flash-image",
    "google/gemini-3-pro-image",
]

ROOMS = [
    ("Hewn Passage",
     "A corridor cut straight through raw rock, tool marks still on the walls. It runs "
     "ahead of you further than the light goes. Grit underfoot, and nothing else."),
    ("The Sump",
     "A low chamber half-flooded with still black water that comes up past your knees. "
     "The ceiling presses down close enough to touch. Something has scratched a tally "
     "into the wall above the waterline."),
    ("Lantern Ledge",
     "A wide gallery with a stone shelf running along the left wall, and on it a lantern "
     "someone left burning. The light reaches maybe half the room; the far end is a wall "
     "of dark with an opening somewhere in it."),
    ("The Fall",
     "The floor has given way across the middle of the room, leaving a shaft that drops "
     "further than a thrown stone reports. A ledge of intact floor runs around the right "
     "side. The walls above are stacked brick, older than the tunnels."),
    ("The Sorting Room",
     "A dry square chamber, orderly in a way nothing else down here is. Shelves cut into "
     "three walls, most of them empty, a few holding stacked bones arranged by size. "
     "A doorway straight ahead, and no dust on the floor."),
]

OUT = Path(__file__).resolve().parent.parent / "scratch"
IMGS = OUT / "img"
DATA = OUT / "palette.json"
PAGE = OUT / "palette.html"


def prompt(name: str, description: str) -> str:
    return (
        "Retro 16-bit pixel art game background of a dungeon room, viewed straight on from "
        "the doorway: ceiling above, far wall in the middle, stone floor receding away "
        "below.\n"
        f"The room is '{name}': {description}\n"
        "Underground and lit only by whatever light is in the room, but use the full tonal "
        "range — deep shadow in the corners through mid-tone stone to bright highlight "
        "where light strikes. Colour the light warm and the shadows cool so they separate. "
        "Flat blocky shading, hard edges, no anti-aliasing. Wide landscape framing. No "
        "text, no characters, no interface, no border."
    )


# --- quantizing --------------------------------------------------------------

def _encode(img_p, size: int) -> dict:
    """A paletted PIL image -> palette + two hex characters per pixel."""
    pal = img_p.getpalette()[: size * 3]
    colours = ["#%02x%02x%02x" % tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
    data = list(img_p.getdata())
    rows = ["".join(f"{data[y * WIDTH + x] % size:02x}" for x in range(WIDTH))
            for y in range(HEIGHT)]
    used = len({data[i] % size for i in range(len(data))})
    return {"palette": colours, "rows": rows, "used": used}


def variants(png: bytes) -> dict[str, dict]:
    from PIL import Image, ImageOps

    small = Image.open(io.BytesIO(png)).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    small = ImageOps.autocontrast(small, cutoff=1)
    out = {}

    # Fixed DB16, dithered. Pad the palette by repeating so unused slots can't be chosen.
    ref = Image.new("P", (1, 1))
    flat = [v for c in DB16 for v in c]
    ref.putpalette(flat * 16)
    out["db16"] = _encode(small.quantize(palette=ref, dither=Image.Dither.FLOYDSTEINBERG), 16)

    for n in (16, 64, 256):
        out[f"a{n}"] = _encode(
            small.convert("P", palette=Image.Palette.ADAPTIVE, colors=n,
                          dither=Image.Dither.FLOYDSTEINBERG), n)
    return out


# --- driver ------------------------------------------------------------------

def fetch(model: str, name: str, description: str, path: Path) -> tuple[float, float]:
    started = time.monotonic()
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt(name, description)}],
              "modalities": ["image", "text"], "usage": {"include": True}},
        timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    d = r.json()
    images = d["choices"][0]["message"].get("images") or []
    if not images:
        raise RuntimeError("model returned no image")
    path.write_bytes(base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1]))
    return float(d.get("usage", {}).get("cost", 0) or 0), round(time.monotonic() - started, 1)


def run(reuse: bool) -> list[dict]:
    IMGS.mkdir(parents=True, exist_ok=True)
    results = []
    for name, description in ROOMS:
        for model in MODELS:
            slug = f"{name.lower().replace(' ', '-')}--{model.split('/')[-1]}.png"
            path = IMGS / slug
            rec = {"room": name, "description": description, "model": model,
                   "cost": 0.0, "seconds": 0.0}
            try:
                if not (reuse and path.exists()):
                    print(f"  {model:<36} {name}", flush=True)
                    rec["cost"], rec["seconds"] = fetch(model, name, description, path)
                rec["variants"] = variants(path.read_bytes())
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"[:220]
                print(f"      failed: {rec['error'][:110]}", flush=True)
            results.append(rec)
    return results


VARIANTS = [
    ("db16", "DawnBringer 16", "The fixed 16-colour palette, dithered."),
    ("a16", "16, per image", "Sixteen colours chosen from what is actually in the picture."),
    ("a64", "64, per image", "Sixty-four."),
    ("a256", "256, per image", "Two hundred and fifty-six."),
]


def write_page(results: list[dict]) -> None:
    PAGE.write_text(TEMPLATE.replace("/*__DATA__*/null", json.dumps(
        {"width": WIDTH, "height": HEIGHT, "variants": VARIANTS, "results": results})))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Palette Size Test</title>
<style>
  :root{--ink:#120d18;--panel:#1a1422;--line:#31283c;--text:#deeed6;--dim:#8595a1;
        --gold:#dad45e;--bad:#d04648;
        --mono:ui-monospace,"IBM Plex Mono",Menlo,monospace;
        --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
  *{box-sizing:border-box}
  body{margin:0;padding:32px 24px 64px;background:var(--ink);color:var(--text);
       font:15px/1.6 var(--sans)}
  .wrap{max-width:1240px;margin:0 auto}
  h1{font:700 13px/1 var(--mono);letter-spacing:.32em;text-transform:uppercase;
     color:var(--gold);margin:0 0 10px}
  .lede{color:var(--dim);max-width:72ch;margin:0 0 24px}
  .key{display:flex;flex-wrap:wrap;gap:14px;padding:14px 16px;background:var(--panel);
       border:1px solid var(--line);margin-bottom:26px}
  .key div{flex:1 1 200px}
  .key b{display:block;font:700 11px/1.4 var(--mono);letter-spacing:.16em;
         text-transform:uppercase}
  .key span{color:var(--dim);font-size:13px}
  table{width:100%;border-collapse:collapse;margin:0 0 30px;
        font:13px/1.5 var(--mono);font-variant-numeric:tabular-nums}
  th{text-align:left;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
     color:var(--dim);border-bottom:1px solid var(--line);padding:0 12px 8px 0}
  td{padding:7px 12px 7px 0;border-bottom:1px solid var(--line)}
  td.n{text-align:right}
  .room{padding-top:26px;border-top:1px solid var(--line);margin-bottom:34px}
  .room h2{font:700 16px/1.3 var(--sans);color:var(--gold);margin:0 0 4px}
  .room p{color:var(--dim);max-width:80ch;margin:0 0 18px}
  .model{margin-bottom:22px}
  .model h3{font:11px/1 var(--mono);letter-spacing:.16em;margin:0 0 10px;
            padding-bottom:8px;border-bottom:1px dashed var(--line)}
  .cards{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
  .who{font:11px/1.4 var(--mono);color:var(--dim);margin-bottom:6px}
  canvas{width:100%;display:block;image-rendering:pixelated;background:#000;
         border:1px solid var(--line)}
  .meta{font:11px/1.6 var(--mono);color:var(--dim);margin-top:5px}
  .err{border:1px solid var(--bad);color:var(--bad);padding:10px;
       font:11px/1.5 var(--mono);white-space:pre-wrap}
  footer{margin-top:30px;padding-top:16px;border-top:1px solid var(--line);
         color:var(--dim);font:13px/1.7 var(--mono)}
</style></head><body><div class="wrap">
<h1>Palette Size Test</h1>
<p class="lede">Same generated image every time, quantized four ways with Floyd-Steinberg
dithering. Palette size is ours to choose, so these four cost the same as one.</p>
<div class="key" id="key"></div>
<table><thead><tr><th>Model</th><th class="n">Images</th><th class="n">DB16</th>
<th class="n">16</th><th class="n">64</th><th class="n">256</th>
<th class="n">Cost</th></tr></thead><tbody id="score"></tbody></table>
<div id="out"></div><footer id="foot"></footer></div>
<script>
const D = /*__DATA__*/null;
document.getElementById('key').innerHTML =
  D.variants.map(([k,t,d])=>`<div><b>${t}</b><span>${d}</span></div>`).join('');

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

const score=document.getElementById('score');
for(const m of [...new Set(D.results.map(r=>r.model))]){
  const mine=D.results.filter(r=>r.model===m), ok=mine.filter(r=>r.variants);
  const avg=k=>ok.length?(ok.reduce((a,r)=>a+r.variants[k].used,0)/ok.length).toFixed(1):'—';
  const tr=document.createElement('tr');
  tr.innerHTML=`<td>${m}</td><td class="n">${ok.length}/${mine.length}</td>`+
    D.variants.map(([k])=>`<td class="n">${avg(k)}</td>`).join('')+
    `<td class="n">$${mine.reduce((a,r)=>a+(r.cost||0),0).toFixed(3)}</td>`;
  score.appendChild(tr);
}

const out=document.getElementById('out');
for(const room of [...new Set(D.results.map(r=>r.room))]){
  const mine=D.results.filter(r=>r.room===room);
  const sec=document.createElement('section'); sec.className='room';
  const h=document.createElement('h2'); h.textContent=room;
  const p=document.createElement('p'); p.textContent=mine[0].description;
  sec.append(h,p);
  for(const r of mine){
    const blk=document.createElement('div'); blk.className='model';
    const h3=document.createElement('h3'); h3.textContent=r.model; blk.appendChild(h3);
    const cards=document.createElement('div'); cards.className='cards';
    if(r.variants){
      for(const [k,t] of D.variants){
        const c=document.createElement('div');
        const w=document.createElement('div'); w.className='who'; w.textContent=t;
        c.append(w, draw(r.variants[k]));
        const meta=document.createElement('div'); meta.className='meta';
        meta.textContent=`${r.variants[k].used} colours used`;
        c.appendChild(meta); cards.appendChild(c);
      }
    } else {
      const e=document.createElement('div'); e.className='err';
      e.textContent=r.error||'no output'; cards.appendChild(e);
    }
    blk.appendChild(cards); sec.appendChild(blk);
  }
  out.appendChild(sec);
}
document.getElementById('foot').textContent =
  `${D.results.filter(r=>r.variants).length}/${D.results.length} images · `+
  `total $${D.results.reduce((a,r)=>a+(r.cost||0),0).toFixed(3)}`;
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", action="store_true", help="reuse saved images, requantize only")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    results = run(reuse=args.page)
    DATA.write_text(json.dumps(results, indent=1))
    write_page(results)
    print(f"\n{PAGE}")


if __name__ == "__main__":
    main()
