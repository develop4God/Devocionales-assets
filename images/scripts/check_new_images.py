#!/usr/bin/env python3
"""
check_new_images.py

Simple pre-add check for new devotional images sitting in a staging folder,
before they get moved into images/devotionals/.

Checks:
  1. Format/orientation/minimum size — same rules as images/devotionals/
     (webp, landscape, >= MIN_WIDTH px wide), via the shared
     .github/scripts/devotionals_checks.py module.
  2. Name collisions — filenames that already exist in images/devotionals/.
  3. Duplicate content — both exact pixel-data match and near-duplicate
     (perceptual hash, catches a crop/resize/re-export of the same photo),
     within the staging folder and against images/devotionals/.

Report-only, never deletes or moves anything.

Usage:
  python3 images/scripts/check_new_images.py <staging_folder>
"""

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / '.github' / 'scripts'))

import imagehash
from PIL import Image  # noqa: E402
from devotionals_checks import DEVOTIONALS_DIR, ALLOWED_EXTENSION  # noqa: E402

# Smallest image currently in images/devotionals/ is 870px wide; 800 gives
# a small margin below that without letting through anything as small as
# the 500x333 test file that slipped through the exact-hash-only check.
MIN_WIDTH = 800

# Perceptual-hash distance below which two images count as near-duplicates
# (a crop/resize/re-export of the same photo). 0 = pixel-identical after
# normalization; verified against a real duplicate pair in this repo
# (lambs_shepherd.webp vs. a differently-cropped re-export of the same
# photo) which measured a distance of 2.
PHASH_MAX_DISTANCE = 4


def pixel_hash(path):
    """SHA256 of raw pixel data — catches the same image re-saved/renamed,
    ignores metadata/filename/compression differences."""
    with Image.open(path) as img:
        return hashlib.sha256(img.convert('RGB').tobytes()).hexdigest()


def perceptual_hash(path):
    with Image.open(path) as img:
        return imagehash.phash(img)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', help='Staging folder with new candidate images')
    args = parser.parse_args()

    staging = Path(args.folder).expanduser()
    if not staging.is_dir():
        print(f"✗ Folder not found: {staging}")
        return 1

    live_dir = REPO_ROOT / DEVOTIONALS_DIR
    candidates = sorted(f for f in staging.iterdir() if f.is_file())
    if not candidates:
        print(f"No files found in {staging}")
        return 0

    print(f"→ Checking {len(candidates)} file(s) in {staging}\n")

    # 1. Format + orientation
    wrong_ext = [f for f in candidates if f.suffix.lower() != ALLOWED_EXTENSION]
    ok_files = [f for f in candidates if f.suffix.lower() == ALLOWED_EXTENSION]

    if wrong_ext:
        print(f"WRONG EXTENSION (only {ALLOWED_EXTENSION} is allowed):")
        for f in wrong_ext:
            print(f"  {f.name}")
        print()

    portrait = []
    too_small = []
    unreadable = []
    for f in ok_files:
        try:
            with Image.open(f) as img:
                w, h = img.size
            if w <= h:
                portrait.append((f.name, w, h))
            if w < MIN_WIDTH:
                too_small.append((f.name, w, h))
        except Exception as exc:
            unreadable.append((f.name, str(exc)))

    if unreadable:
        print("UNREADABLE:")
        for name, err in unreadable:
            print(f"  {name}: {err}")
        print()

    if portrait:
        print("WRONG ORIENTATION (portrait/square, expected landscape):")
        for name, w, h in portrait:
            print(f"  {name}: {w}x{h}")
        print()

    if too_small:
        print(f"TOO SMALL (width must be >= {MIN_WIDTH}px):")
        for name, w, h in too_small:
            print(f"  {name}: {w}x{h}")
        print()

    # 2. Name collisions against the live folder
    live_names = {f.name for f in live_dir.glob(f'*{ALLOWED_EXTENSION}')} if live_dir.is_dir() else set()
    name_collisions = [f.name for f in ok_files if f.name in live_names]

    if name_collisions:
        print("NAME ALREADY EXISTS in images/devotionals/:")
        for name in name_collisions:
            print(f"  {name}")
        print()

    # 3. Duplicate content — within staging, and against the live folder
    readable = [f for f in ok_files if f.name not in {n for n, _ in unreadable}]
    hashes = {}  # hash -> list of (source_label, filename)
    for f in readable:
        try:
            h = pixel_hash(f)
        except Exception as exc:
            print(f"  ✗ Could not hash {f.name}: {exc}")
            continue
        hashes.setdefault(h, []).append(('staging', f.name))

    if live_dir.is_dir():
        for f in live_dir.glob(f'*{ALLOWED_EXTENSION}'):
            try:
                h = pixel_hash(f)
            except Exception:
                continue
            hashes.setdefault(h, []).append(('devotionals', f.name))

    dupes = {h: entries for h, entries in hashes.items() if len(entries) > 1}
    if dupes:
        print("DUPLICATE CONTENT (identical image, different name/location):")
        for entries in dupes.values():
            label = ", ".join(f"{loc}/{name}" for loc, name in entries)
            print(f"  {label}")
        print()

    # 4. Near-duplicate content — perceptual hash, catches a crop/resize/
    # re-export of the same photo that exact pixel hashing (above) misses.
    phashes = []  # [(source_label, filename, hash), ...]
    for f in readable:
        try:
            phashes.append(('staging', f.name, perceptual_hash(f)))
        except Exception:
            pass
    if live_dir.is_dir():
        for f in live_dir.glob(f'*{ALLOWED_EXTENSION}'):
            try:
                phashes.append(('devotionals', f.name, perceptual_hash(f)))
            except Exception:
                pass

    near_dupes = []
    seen_pairs = set()
    for i, (loc_a, name_a, hash_a) in enumerate(phashes):
        for loc_b, name_b, hash_b in phashes[i + 1:]:
            if loc_a == loc_b == 'devotionals':
                continue  # both already live — not a new-image problem
            distance = hash_a - hash_b
            if distance <= PHASH_MAX_DISTANCE:
                pair = frozenset([(loc_a, name_a), (loc_b, name_b)])
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    near_dupes.append((loc_a, name_a, loc_b, name_b, distance))

    if near_dupes:
        print("NEAR-DUPLICATE CONTENT (looks like the same photo, cropped/resized/re-exported):")
        for loc_a, name_a, loc_b, name_b, distance in near_dupes:
            print(f"  {loc_a}/{name_a}  ~=  {loc_b}/{name_b}  (distance {distance})")
        print()

    problems = wrong_ext or unreadable or portrait or too_small or name_collisions or dupes or near_dupes
    if not problems:
        print(f"OK: {len(ok_files)} file(s) valid, no name collisions, no duplicates.")
        return 0

    print("Fix the issues above before moving these into images/devotionals/.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
