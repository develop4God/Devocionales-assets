#!/usr/bin/env python3
"""
encounter_images.py

Pipeline for Encounters image assets:
  1. List branches on GitHub and let the user pick one.
  2. Fetch encounters/index.json from that branch and let the user pick an encounter + language.
  3. Extract every "image_url" filename referenced in the JSON.
  4. Validate against the matching local images folder (missing / extra files).
  5. If validation passes, convert all PNGs to AVIF (avifenc, slowest/best quality,
     original resolution, 4:4:4 chroma).
  6. Reverse-validate: every source PNG has a matching .avif output.
  7. Print a size/compression stats table.

Runs as phases (each can be run standalone), or all together with no subcommand.

  python3 encounter_images.py all \
      --repo develop4God/devocionales-json \
      --branch claude/bible-discovery-studies-srg032 \
      --path encounters/es/nicodemus_es_001.json \
      --folder ~/Projects/Devocionales-assets/images/encounters/nicodemus_001

  python3 encounter_images.py fetch --repo ... --branch ... --path ...
  python3 encounter_images.py compare --path ... --folder ...
  python3 encounter_images.py convert --path ... --folder ...
  python3 encounter_images.py commit --folder /path/to/images/encounters/nicodemus_001

The `commit` phase only needs --folder: it stages/commits/pushes whatever is
already sitting in that folder (PNG+AVIF pairs already produced), asking for
confirmation before pushing. Use this when images are already converted and
you just need to commit + push.

Requires: requests (pip install requests --break-system-packages)
Requires on PATH: avifenc
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

RAW_URL_TMPL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"
API_BRANCHES_TMPL = "https://api.github.com/repos/{repo}/branches"
IMAGES_ROOT = Path(__file__).resolve().parent.parent / "encounters"


def prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{msg}{suffix}: ").strip()
    return val or default


def choose_from_list(items: list[str], title: str) -> str:
    print(f"\n{title}")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}")
    while True:
        choice = input(f"Select 1-{len(items)}: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            return items[int(choice) - 1]
        print("Invalid choice, try again.")


def list_branches(repo: str) -> list[str]:
    url = API_BRANCHES_TMPL.format(repo=repo)
    branches = []
    page = 1
    while True:
        resp = requests.get(url, params={"per_page": 100, "page": page}, timeout=30)
        if resp.status_code != 200:
            print(f"✗ Failed to list branches (HTTP {resp.status_code}).")
            sys.exit(1)
        batch = resp.json()
        if not batch:
            break
        branches.extend(b["name"] for b in batch)
        page += 1
    return branches


def resolve_repo_branch_path(args):
    """Interactively fill in repo/branch/path/folder when missing (used by fetch/all)."""
    if not args.repo:
        args.repo = prompt("GitHub repo (owner/name)", "develop4God/devocionales-json")

    if not args.branch:
        print(f"\n→ Fetching branches for {args.repo}...")
        branches = list_branches(args.repo)
        if not branches:
            print("✗ No branches found.")
            sys.exit(1)
        branches.sort(key=lambda b: (b != "main", b))
        args.branch = choose_from_list(branches, "Available branches:")

    if not args.path:
        index = fetch_json(args.repo, args.branch, "encounters/index.json")
        encounters = index.get("encounters", [])
        if not encounters:
            print("✗ No encounters found in encounters/index.json.")
            sys.exit(1)
        ids = [e["id"] for e in encounters]
        chosen_id = choose_from_list(ids, "Available encounters:")
        encounter = next(e for e in encounters if e["id"] == chosen_id)
        filename = encounter["files"]["es"]
        args.path = f"encounters/es/{filename}"

        if not args.folder:
            default_folder = IMAGES_ROOT / chosen_id
            if default_folder.is_dir():
                args.folder = str(default_folder)

    if not args.folder:
        args.folder = prompt("Local images folder path")


def fetch_json(repo: str, branch: str, path: str) -> dict:
    url = RAW_URL_TMPL.format(repo=repo, branch=branch, path=path)
    print(f"\n→ Fetching: {url}")
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        print(f"✗ Failed to fetch JSON (HTTP {resp.status_code}). Check repo/branch/path.")
        sys.exit(1)
    return json.loads(resp.text)


def extract_image_filenames(data: dict) -> list[str]:
    """Walk the JSON recursively and collect every 'image_url' value."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "image_url" and isinstance(v, str):
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def validate_folder(expected: list[str], folder: Path) -> bool:
    print(f"\n→ Validating against: {folder}")
    existing = {f.name for f in folder.glob("*.png")}
    expected_set = set(expected)

    missing = expected_set - existing
    extra = existing - expected_set

    all_ok = True
    for name in expected:
        if name in existing:
            print(f"  OK      {name}")
        else:
            print(f"  MISSING {name}")
            all_ok = False

    if extra:
        print("\n  Extra PNGs in folder not referenced by JSON:")
        for name in sorted(extra):
            print(f"  EXTRA   {name}")

    print(f"\n  Expected: {len(expected)}  Found: {len(existing & expected_set)}  Missing: {len(missing)}  Extra: {len(extra)}")
    return all_ok


def human_size(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def convert_to_avif(filenames: list[str], folder: Path) -> Path:
    """Convert PNGs to AVIF in-place (same folder, no subfolder)."""
    if not shutil.which("avifenc"):
        print("\n✗ avifenc not found on PATH. Install it first (e.g. `sudo apt install libavif-bin`).")
        sys.exit(1)

    print(f"\n→ Converting {len(filenames)} PNGs to AVIF (speed 0, qcolor 75, qalpha 75, yuv444)...")
    for name in filenames:
        src = folder / name
        dst = folder / (Path(name).stem + ".avif")
        cmd = [
            "avifenc",
            "--speed", "0",
            "--qcolor", "75",
            "--qalpha", "75",
            "--yuv", "444",
            str(src),
            str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ✗ FAILED {name}\n{result.stderr}")
        else:
            print(f"  ✓ {name} -> {dst.name}")

    return folder


def reverse_validate_and_stats(filenames: list[str], folder: Path, out_dir: Path) -> bool:
    print("\n→ Reverse validation + stats:\n")
    header = f"{'File':40} {'PNG':>10} {'AVIF':>10} {'Saved':>8}"
    print(header)
    print("-" * len(header))

    total_png = 0
    total_avif = 0
    all_ok = True

    for name in filenames:
        png_path = folder / name
        avif_path = out_dir / (Path(name).stem + ".avif")

        if not avif_path.exists():
            print(f"{name:40} {'MISSING AVIF':>10}")
            all_ok = False
            continue

        png_size = png_path.stat().st_size
        avif_size = avif_path.stat().st_size
        saved_pct = 100 * (1 - avif_size / png_size) if png_size else 0

        total_png += png_size
        total_avif += avif_size

        print(f"{name:40} {human_size(png_size):>10} {human_size(avif_size):>10} {saved_pct:7.1f}%")

    print("-" * len(header))
    if total_png:
        total_saved_pct = 100 * (1 - total_avif / total_png)
        print(f"{'TOTAL':40} {human_size(total_png):>10} {human_size(total_avif):>10} {total_saved_pct:7.1f}%")

    print(f"\n{'✓ All AVIF files present.' if all_ok else '✗ Some AVIF files are missing — check log above.'}")
    return all_ok


def git_repo_root(folder: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


STATUS_LABELS = {
    "??": "new",
    "A ": "added",
    "M ": "modified",
    " M": "modified",
    "MM": "modified",
    "D ": "deleted",
    " D": "deleted",
    "R ": "renamed",
}


def describe_status(status: str) -> list[str]:
    """Turn 'git status --porcelain -uall' lines into plain-English descriptions."""
    lines = []
    for entry in status.splitlines():
        code, path = entry[:2], entry[3:]
        label = STATUS_LABELS.get(code, code.strip() or "changed")
        lines.append(f"  {label:10} {path}")
    return lines



def check_repo_wide_pending(repo_root: Path, folder: Path):
    """Warn if there are git changes outside the target folder."""
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "-uall"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not status:
        return
    folder_rel = str(folder.relative_to(repo_root))
    outside = [line for line in status.splitlines() if not line[3:].startswith(folder_rel)]
    if outside:
        print(f"\n⚠ WARNING: {len(outside)} change(s) exist OUTSIDE {folder_rel}:")
        for line in describe_status("\n".join(outside)):
            print(line)
        confirm = input("\nThese will NOT be included, but they're sitting pending. Continue anyway? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            sys.exit(1)


def git_commit_and_push(folder: Path, encounter_id: str):
    repo_root = git_repo_root(folder)
    if repo_root is None:
        print("\n(Skipping git commit — folder is not inside a git repo.)")
        return

    check_repo_wide_pending(repo_root, folder)

    branch = subprocess.run(
        ["git", "-C", str(repo_root), "branch", "--show-current"],
        capture_output=True, text=True,
    ).stdout.strip()

    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "-uall", str(folder)],
        capture_output=True, text=True,
    ).stdout.strip()

    if not status:
        print("\n(No git changes to commit.)")
        return

    file_lines = describe_status(status)
    message = f"feat({encounter_id}): add encounter images and AVIF conversions"

    print(f"\n→ Branch: {branch}")
    print(f"→ Files to commit ({len(file_lines)}):")
    for line in file_lines:
        print(line)
    print(f"\n→ Commit message:\n  {message}")

    confirm = input(f"\nCommit & push to origin/{branch}? [Y/n]: ").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("Aborted — no commit made.")
        return

    subprocess.run(["git", "-C", str(repo_root), "add", str(folder)], check=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", message], check=True)
    subprocess.run(["git", "-C", str(repo_root), "push", "origin", branch], check=True)
    print("✓ Pushed.")


def cmd_fetch(args) -> dict:
    resolve_repo_branch_path(args)
    return fetch_json(args.repo, args.branch, args.path)


def cmd_compare(args, data: dict | None = None) -> list[str]:
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"✗ Folder not found: {folder}")
        sys.exit(1)

    if data is None:
        data = fetch_json(args.repo, args.branch, args.path)

    filenames = extract_image_filenames(data)
    if not filenames:
        print("✗ No 'image_url' fields found in JSON.")
        sys.exit(1)

    print(f"\n→ Extracted {len(filenames)} image references from JSON.")

    if not validate_folder(filenames, folder):
        print("\n✗ Validation failed — fix missing files before converting. Aborting.")
        sys.exit(1)

    print("\n✓ Validation passed — all files present.")
    return filenames


def cmd_convert(args, filenames: list[str] | None = None):
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"✗ Folder not found: {folder}")
        sys.exit(1)

    if filenames is None:
        filenames = [f.name for f in folder.glob("*.png")]
        if not filenames:
            print("✗ No PNG files found in folder.")
            sys.exit(1)

    out_dir = convert_to_avif(filenames, folder)
    all_ok = reverse_validate_and_stats(filenames, folder, out_dir)

    if not all_ok:
        print("\n✗ Not all PNGs have a corresponding AVIF.")
        sys.exit(1)


def cmd_commit(args):
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"✗ Folder not found: {folder}")
        sys.exit(1)
    git_commit_and_push(folder, folder.name)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate + convert Encounter images to AVIF, phase by phase.")
    p.set_defaults(repo=None, branch=None, path=None, folder=None)
    sub = p.add_subparsers(dest="phase")

    def add_common(sp, needs_folder=True, needs_source=True):
        if needs_source:
            sp.add_argument("--repo", help="GitHub repo, e.g. develop4God/devocionales-json")
            sp.add_argument("--branch", help="Branch name")
            sp.add_argument("--path", help="Path to JSON file within repo")
        if needs_folder:
            sp.add_argument("--folder", help="Local images folder path")

    add_common(sub.add_parser("all", help="Run fetch -> compare -> convert -> commit"))
    add_common(sub.add_parser("fetch", help="Fetch the encounter JSON only"), needs_folder=False)
    add_common(sub.add_parser("compare", help="Validate local folder against JSON image_url list"))
    add_common(sub.add_parser("convert", help="Convert PNGs to AVIF + reverse-validate"))
    add_common(sub.add_parser("commit", help="Commit + push an already-converted folder"), needs_source=False)

    return p


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    phase = args.phase or "all"

    if phase == "fetch":
        cmd_fetch(args)
        return

    if phase == "compare":
        resolve_repo_branch_path(args)
        cmd_compare(args)
        return

    if phase == "convert":
        if not args.folder:
            args.folder = prompt("Local images folder path")
        cmd_convert(args)
        return

    if phase == "commit":
        if not args.folder:
            args.folder = prompt("Local images folder path")
        cmd_commit(args)
        return

    # phase == "all"
    resolve_repo_branch_path(args)
    data = fetch_json(args.repo, args.branch, args.path)
    filenames = cmd_compare(args, data=data)
    cmd_convert(args, filenames=filenames)
    cmd_commit(args)


if __name__ == "__main__":
    main()
