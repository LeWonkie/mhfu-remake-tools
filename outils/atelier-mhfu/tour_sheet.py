"""Contact sheets for zone_tour.py output (read-only on runs).

usage: tour_sheet.py TOUR_DIR   -> TOUR_DIR/tour-<quest>.png, plus per-area
mean luminance and vertex-space warnings in TOUR_DIR/tour-summary.json.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

DATA = Path(__file__).resolve().parents[2] / "atelier-psprecomp"


def font(size):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def main(folder: Path) -> None:
    tour = json.loads((folder / "tour.json").read_text(encoding="utf-8"))
    summary = []
    for item in tour["tours"]:
        run = DATA / "runs" / item.get("run_id", "")
        log = (run / "stdout.txt").read_text(encoding="utf-8", errors="replace") if run.is_dir() else ""
        areas = sorted(item.get("areas", {}).items(), key=lambda kv: int(kv[0]))
        cols, w, h = 4, 384, 218
        rows = max(1, (len(areas) + cols - 1) // cols)
        sheet = Image.new("RGB", (cols * w, rows * (h + 24) + 30), "#171a20")
        draw = ImageDraw.Draw(sheet)
        draw.text((6, 6), f"Quete {item['quest']} | {item.get('run_id')} | {item.get('state')}",
                  font=font(16), fill="#ffffff")
        for i, (area, info) in enumerate(areas):
            x, y = (i % cols) * w, 30 + (i // cols) * (h + 24)
            path = run / "frames" / info["capture"]
            mean = None
            if path.is_file():
                with Image.open(path) as source:
                    rgb = source.convert("RGB")
                    mean = round(ImageStat.Stat(rgb.convert("L")).mean[0], 1)
                    sheet.paste(rgb.resize((w - 4, h - 4)), (x + 2, y + 22))
            draw.text((x + 4, y + 2), f"zone {area} -> {info['exits']}  lum {mean}", font=font(13), fill="#c7d0df")
            summary.append({"quest": item["quest"], "area": int(area), "exits": info["exits"],
                            "mean_luminance": mean, "capture": str(path)})
        sheet.save(folder / f"tour-{item['quest']}.png")
        summary.append({"quest": item["quest"], "state": item.get("state"),
                        "vertex_space_warnings": log.count("ran out of vertex space"),
                        "hops": len(item.get("hops", []))})
    (folder / "tour-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps([s for s in summary if "state" in s]))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
