import json
import os
import sys
from datetime import datetime, timezone

from devotionals_checks import (
    DEVOTIONALS_DIR,
    INDEX_PATH,
    ALLOWED_EXTENSION,
    find_wrong_extension_files,
    find_bad_orientation_files,
    list_webp_files,
    fingerprint,
)


def remove_wrong_extension_files():
    """Delete any file that isn't .webp — this repo's images/devotionals/
    is WebP-only by convention. Deletes immediately rather than just
    excluding from the manifest, so a wrong-format file dropped in by
    mistake doesn't linger in the folder; run locally, you see it happen.
    In CI this still runs, and main() reports it as a failure (see below)
    so a deletion is never silent even when nobody's watching the run."""
    removed = []
    for f in find_wrong_extension_files():
        os.remove(os.path.join(DEVOTIONALS_DIR, f))
        removed.append(f)
    return removed


def remove_bad_orientation_files(files):
    """Delete any non-landscape (width <= height) image."""
    bad = find_bad_orientation_files(files)
    for f, _w, _h in bad:
        os.remove(os.path.join(DEVOTIONALS_DIR, f))
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
    # Wrong-format gate: deletes any non-.webp file immediately, before
    # attempting to decode anything. Runs first so a stray .jpg doesn't
    # reach the dimension check and produce a confusing decode-failure
    # error instead of a clear "wrong format" one.
    removed_ext = remove_wrong_extension_files()
    if removed_ext:
        print(f"REMOVED — wrong file extension (only {ALLOWED_EXTENSION} is allowed):", file=sys.stderr)
        for f in removed_ext:
            print(f"  {f}", file=sys.stderr)

    # Orientation gate: deletes any non-landscape .webp file. Both gates
    # delete rather than just excluding from the manifest — see each
    # function's docstring — and either one deleting anything fails this
    # run (return 1 below) so a deletion is never silent, even in CI.
    removed_orientation = remove_bad_orientation_files(list_webp_files())
    if removed_orientation:
        print("REMOVED — wrong orientation (portrait/square, expected landscape):", file=sys.stderr)
        for f, w, h in removed_orientation:
            print(f"  {f}: {w}x{h}", file=sys.stderr)

    if removed_ext or removed_orientation:
        print("One or more bad files were deleted — re-run to generate index.json "
              "from the now-clean folder.", file=sys.stderr)
        return 1

    files = list_webp_files()
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
    # Re-list disk, don't trust the in-memory `files` var. list_webp_files
    # (not list_real_files) is correct here: by this point both gates
    # above have already deleted anything that isn't a valid landscape
    # .webp, so this should exactly match — using list_real_files would
    # silently reintroduce non-.webp files into the comparison if a gate
    # above is ever changed to not delete unconditionally.
    real_files = set(list_webp_files())

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
