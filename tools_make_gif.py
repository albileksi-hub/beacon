"""Render the landing-page lighthouse to an animated GIF.

CSS animations cannot be stepped from outside the page, and headless Chrome
renders a deterministic frame regardless of --virtual-time-budget. So each
frame is a separate page load that freezes every animation at one instant:
`animation-play-state: paused` with a negative `animation-delay` samples the
timeline at exactly that point.

The real timings do not share a period -- a 7s beam against a 15s alternating
sway is a 42s cycle -- so the durations are retimed to divide an 8s loop. The
motion is the same motion; only its speed differs from the live page.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "pylibs"))

from PIL import Image  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = Path(r"C:\Users\Albi\beacon")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

LOOP_SECONDS = 8.0
FPS = 10
FRAMES = int(LOOP_SECONDS * FPS)

STAGE_W, STAGE_H = 560, 312
SCALE = 2
# The subject measured at CSS x 135-425, y 0-250, so it needed pushing down off
# the top edge; the crop then keeps it centred with the beams bleeding off.
SCENE_TOP = 30
CROP = (58, 0, 502, 312)          # CSS px within the stage
OUT_W, OUT_H = CROP[2] - CROP[0], CROP[3] - CROP[1]

# Retimed so every cycle divides the 8s loop exactly.
RETIME = """
  .beacon-sway { animation-duration: 4s !important; }   /* alternate -> 8s cycle */
  .beam-spin   { animation-duration: 8s !important; }   /* one revolution */
  .lamp        { animation-duration: 4s !important; }
  .lantern-glow{ animation-duration: 4s !important; }
  .mote        { animation-duration: 4s !important; }   /* alternate -> 8s cycle */
"""


def scene_markup(page: str) -> str:
    """The hero-scene element, matched by walking its nested divs."""
    start = page.index('<div class="hero-scene"')
    depth, i = 0, start
    for match in re.finditer(r"<(/?)div\b", page[start:]):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            i = start + match.end()
            return page[start : page.index(">", i) + 1]
    raise SystemExit("could not find the end of .hero-scene")


def build_frame(css: str, scene: str, at: float) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{css}
html, body {{ margin: 0; padding: 0; background: #0b0906; }}
#stage {{ position: relative; width: {STAGE_W}px; height: {STAGE_H}px;
          overflow: hidden; background: #0b0906; }}
.hero-scene {{ margin: {SCENE_TOP}px auto 0; }}
{RETIME}
/* Freeze every animation at one instant on the retimed timeline. */
*, *::before, *::after {{
  animation-play-state: paused !important;
  animation-delay: -{at:.4f}s !important;
}}
</style></head><body><div id="stage">{scene}</div></body></html>"""


def main() -> int:
    page = (HERE / "raw.html").read_text(encoding="utf-8")
    css = (REPO / "static" / "dashboard.css").read_text(encoding="utf-8")
    scene = scene_markup(page)

    work = HERE / "frames"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()

    print(f"  {FRAMES} frames over {LOOP_SECONDS:g}s at {FPS}fps")
    for n in range(FRAMES):
        at = n * LOOP_SECONDS / FRAMES
        html = work / f"f{n:03d}.html"
        html.write_text(build_frame(css, scene, at), encoding="utf-8")
        subprocess.run(
            [
                CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                f"--force-device-scale-factor={SCALE}",
                f"--window-size={STAGE_W + 140},{STAGE_H + 120}",
                "--virtual-time-budget=1500",
                f"--screenshot={work / f'f{n:03d}.png'}",
                html.as_uri(),
            ],
            capture_output=True,
        )
        if n % 20 == 0:
            print(f"    frame {n}/{FRAMES}")

    shots = sorted(work.glob("*.png"))
    if len(shots) != FRAMES:
        print(f"  only {len(shots)}/{FRAMES} frames rendered")
        return 1

    images = []
    for shot in shots:
        im = Image.open(shot).convert("RGB")
        im = im.crop(tuple(v * SCALE for v in CROP))
        im = im.resize((OUT_W, OUT_H), Image.LANCZOS)
        images.append(im.quantize(colors=128, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG))

    out = REPO / "docs" / "lighthouse.gif"
    images[0].save(
        out, save_all=True, append_images=images[1:],
        duration=int(1000 / FPS), loop=0, optimize=True, disposal=2,
    )
    print(f"  {out.name}  {out.stat().st_size / 1024:.0f} KB  {OUT_W}x{OUT_H}  {FRAMES} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
