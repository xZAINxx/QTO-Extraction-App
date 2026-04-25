"""Download pretrained YOLO weights for symbol detection.

Run from the repo root::

    python -m cv.download_weights

This pulls the FloorPlanCAD-pretrained YOLOv8n weights into
``cv/weights/floorplancad_general.pt``. The file is gitignored — every
clean checkout must re-download.

If you'd rather use a community fine-tune, set ``QTO_YOLO_WEIGHTS_URL``
in the env before running this script. The default falls back to
ultralytics' ``yolov8n.pt`` (general COCO, *not* floorplan-specific) so
the tool stays runnable until a project-specific weight is available.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.request
from pathlib import Path


WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"
DEFAULT_URL = os.environ.get(
    "QTO_YOLO_WEIGHTS_URL",
    "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt",
)
DEFAULT_NAME = "floorplancad_general.pt"


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(
    url: str = DEFAULT_URL,
    dest_name: str = DEFAULT_NAME,
    *,
    force: bool = False,
) -> Path:
    """Fetch ``url`` into ``cv/weights/<dest_name>``.

    Returns the on-disk Path on success.
    """
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = WEIGHTS_DIR / dest_name
    if dest.exists() and not force:
        size = dest.stat().st_size
        print(f"[ok] weights already present: {dest} ({_human_bytes(size)})")
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"[..] downloading {url}")
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        copied = 0
        chunk = 1 << 20
        while True:
            buf = resp.read(chunk)
            if not buf:
                break
            out.write(buf)
            copied += len(buf)
            if total:
                pct = copied * 100 / total
                print(f"\r    {_human_bytes(copied)} / {_human_bytes(total)} "
                      f"({pct:.1f}%)", end="", file=sys.stderr)
        print("", file=sys.stderr)

    shutil.move(tmp, dest)
    print(f"[ok] saved to {dest} (sha256={_sha256(dest)[:16]}…)")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download YOLO weights for QTO symbol detection")
    ap.add_argument("--url", default=DEFAULT_URL, help="Source URL (override via QTO_YOLO_WEIGHTS_URL env)")
    ap.add_argument("--name", default=DEFAULT_NAME, help="Destination filename in cv/weights/")
    ap.add_argument("--force", action="store_true", help="Re-download even if file already exists")
    args = ap.parse_args(argv)

    try:
        download(args.url, args.name, force=args.force)
    except Exception as e:
        print(f"[err] download failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
