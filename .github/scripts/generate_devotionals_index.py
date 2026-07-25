import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

DEVOTIONALS_DIR = 'images/devotionals'
INDEX_PATH = os.path.join(DEVOTIONALS_DIR, 'index.json')


def list_real_files():
    return sorted(
        f for f in os.listdir(DEVOTIONALS_DIR)
        if os.path.isfile(os.path.join(DEVOTIONALS_DIR, f)) and f != 'index.json'
    )


def fingerprint(files):
    return hashlib.sha256('\n'.join(files).encode('utf-8')).hexdigest()[:12]


def get_dimensions(path):
    """Read width/height via avifdec --info (native libavif tooling, no
    Python image-library dependency). Raises RuntimeError if avifdec is
    missing or the file can't be decoded/parsed."""
    try:
        result = subprocess.run(
            ['avifdec', '--info', path],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError("avifdec not found — install libavif (apt-get install libavif-bin)")

    match = re.search(r'Resolution\s*:\s*(\d+)x(\d+)', result.stdout)
    if not match:
        raise RuntimeError(f"could not read resolution from avifdec output for {path}")
    return int(match.group(1)), int(match.group(2))


def validate_orientation(files):
    """Reject any non-landscape (width <= height) image before it's ever
    written into index.json. Landscape-only, no width/resolution
    threshold — verified against every image currently in this repo that
    a plain width>height check cleanly separates known-good landscape
    files (width 870-1332) from known-bad portrait files (width 687-765),
    with no overlap. Revisit if that assumption stops holding."""
    bad = []
    for f in files:
        w, h = get_dimensions(os.path.join(DEVOTIONALS_DIR, f))
        if w <= h:
            bad.append((f, w, h))
    return bad


def load_existing_version():
    if not os.path.exists(INDEX_PATH):
        return None
    try:
        with open(INDEX_PATH, encoding='utf-8') as fh:
            return json.load(fh).get('version')
    except (json.JSONDecodeError, OSError):
        return None


def main():
    files = list_real_files()

    # Orientation gate: run before anything is written, so a bad image
    # never enters the manifest. Fails the whole job (CI never commits)
    # rather than silently excluding the file from index.json — a bad
    # image should be visibly rejected, not quietly dropped.
    bad_orientation = validate_orientation(files)
    if bad_orientation:
        print("ORIENTATION VALIDATION FAILED — non-landscape image(s) found:", file=sys.stderr)
        for f, w, h in bad_orientation:
            print(f"  {f}: {w}x{h} (portrait/square, expected landscape)", file=sys.stderr)
        return 1

    new_hash = fingerprint(files)

    # Pre-write gate: skip entirely if nothing actually changed since the
    # last committed manifest — avoids a no-op commit on every unrelated
    # push that happens to touch this folder's CI trigger path.
    existing_version = load_existing_version()
    if existing_version == new_hash:
        print(f"No change (version {new_hash} already current) — skipping write")
        return 0

    manifest = {
        'version': new_hash,
        'generatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'files': files,
    }
    with open(INDEX_PATH, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write('\n')

    # Post-write reverse validation: read the file back and confirm it
    # matches disk exactly — every listed filename really exists, and
    # nothing real on disk was left out. Catches a bug in generation
    # logic (e.g. a bad filter) rather than silently committing a
    # manifest that lies about what's actually in the folder.
    with open(INDEX_PATH, encoding='utf-8') as fh:
        written = json.load(fh)

    written_files = set(written.get('files', []))
    real_files = set(list_real_files())  # re-list disk, don't trust the in-memory `files` var

    missing_from_manifest = real_files - written_files
    phantom_in_manifest = written_files - real_files

    if missing_from_manifest or phantom_in_manifest:
        print("REVERSE VALIDATION FAILED", file=sys.stderr)
        if missing_from_manifest:
            print(f"  On disk but missing from index.json: {sorted(missing_from_manifest)}", file=sys.stderr)
        if phantom_in_manifest:
            print(f"  Listed in index.json but not on disk: {sorted(phantom_in_manifest)}", file=sys.stderr)
        return 1

    if written.get('version') != new_hash:
        print(f"REVERSE VALIDATION FAILED: written version {written.get('version')!r} != computed {new_hash!r}", file=sys.stderr)
        return 1

    print(f"OK: {len(real_files)} files, version {new_hash}, validated against disk")
    return 0


if __name__ == '__main__':
    sys.exit(main())
