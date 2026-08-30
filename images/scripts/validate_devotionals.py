#!/usr/bin/env python3
"""
validate_devotionals.py

Local pre-push validator for images/devotionals/. Runs the exact same
checks as the CI manifest generator (.github/scripts/generate_devotionals_index.py,
via the shared .github/scripts/devotionals_checks.py module) so problems
show up before you push instead of after a failed CI run.

Report-only by default — nothing is deleted. Pass --fix to actually
delete wrong-extension/wrong-orientation files, mirroring what CI does.

Run from the repo root:
  python3 images/scripts/validate_devotionals.py
  python3 images/scripts/validate_devotionals.py --fix
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / '.github' / 'scripts'))

from devotionals_checks import (  # noqa: E402
    ALLOWED_EXTENSION,
    DEVOTIONALS_DIR,
    find_bad_orientation_files,
    find_wrong_extension_files,
    list_webp_files,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fix', action='store_true',
                         help='Delete wrong-extension/wrong-orientation files instead of just reporting them')
    args = parser.parse_args()

    devotionals_dir = REPO_ROOT / DEVOTIONALS_DIR
    os.chdir(REPO_ROOT)

    wrong_ext = find_wrong_extension_files()
    if wrong_ext:
        print(f"WRONG EXTENSION (only {ALLOWED_EXTENSION} is allowed):")
        for f in wrong_ext:
            print(f"  {f}")
        if args.fix:
            for f in wrong_ext:
                (devotionals_dir / f).unlink()
            print(f"  → deleted {len(wrong_ext)} file(s)")
        print()

    files_to_check = list_webp_files() if not (wrong_ext and args.fix) else list_webp_files()
    bad_orientation = find_bad_orientation_files(files_to_check)
    if bad_orientation:
        print("WRONG ORIENTATION (portrait/square, expected landscape):")
        for f, w, h in bad_orientation:
            print(f"  {f}: {w}x{h}")
        if args.fix:
            for f, _w, _h in bad_orientation:
                (devotionals_dir / f).unlink()
            print(f"  → deleted {len(bad_orientation)} file(s)")
        print()

    if not wrong_ext and not bad_orientation:
        print(f"OK: {len(list_webp_files())} WebP files, all landscape.")
        return 0

    if args.fix:
        print("Fixed — re-run without --fix to confirm a clean folder.")
        return 0

    print("Validation failed. Re-run with --fix to delete the offending files, "
          "or fix them manually before pushing.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
