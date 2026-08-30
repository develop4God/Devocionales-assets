"""Shared validation logic for images/devotionals/, used by both the CI
manifest generator (.github/scripts/generate_devotionals_index.py) and the
local pre-push validator (images/scripts/validate_devotional_images.py). Keeping
this in one module means the two can't silently drift apart — CI stays the
final gate, but the local tool runs the identical checks report-only.
"""

import hashlib
import os

from PIL import Image

DEVOTIONALS_DIR = 'images/devotionals'
INDEX_PATH = os.path.join(DEVOTIONALS_DIR, 'index.json')
ALLOWED_EXTENSION = '.webp'


def list_real_files(devotionals_dir=DEVOTIONALS_DIR):
    """Every non-index file in the folder, any extension."""
    return sorted(
        f for f in os.listdir(devotionals_dir)
        if os.path.isfile(os.path.join(devotionals_dir, f)) and f != 'index.json'
    )


def list_webp_files(devotionals_dir=DEVOTIONALS_DIR):
    return sorted(f for f in list_real_files(devotionals_dir) if f.lower().endswith(ALLOWED_EXTENSION))


def find_wrong_extension_files(devotionals_dir=DEVOTIONALS_DIR):
    return [f for f in list_real_files(devotionals_dir) if not f.lower().endswith(ALLOWED_EXTENSION)]


def get_dimensions(path):
    """Read width/height via Pillow. Raises RuntimeError if the file can't
    be opened/decoded."""
    try:
        with Image.open(path) as img:
            return img.size
    except Exception as exc:
        raise RuntimeError(f"could not read dimensions for {path}: {exc}") from exc


def find_bad_orientation_files(files, devotionals_dir=DEVOTIONALS_DIR):
    """Return [(filename, width, height), ...] for any non-landscape
    (width <= height) image. Landscape-only, no width/resolution
    threshold — verified against every image currently in this repo that
    a plain width>height check cleanly separates known-good landscape
    files (width 870-1332) from known-bad portrait files (width
    687-765), with no overlap. Revisit if that assumption stops holding."""
    bad = []
    for f in files:
        w, h = get_dimensions(os.path.join(devotionals_dir, f))
        if w <= h:
            bad.append((f, w, h))
    return bad


def fingerprint(files):
    return hashlib.sha256('\n'.join(files).encode('utf-8')).hexdigest()[:12]
