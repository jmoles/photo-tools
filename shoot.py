# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "exifread",
# ]
# ///

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import exifread

from photo import (
    ALREADY_RENAMED_RE,
    EXIF_DATE_TAGS,
    SHOOT_PHOTO_EXTS as PHOTO_EXTS,
    VIDEO_EXTS,
    parse_exif_dt,
    rename_file,
)

ALL_EXTS = PHOTO_EXTS | VIDEO_EXTS

# Priority matches organize.py / ingest.py: original capture time wins over
# digitisation timestamp, which wins over last-modified. exifread surfaces
# DateTimeOriginal/Digitized in the EXIF SubIFD; ModifyDate is IFD0's
# 'Image DateTime'. Many Apple/cloud-export JPEGs strip IFD0 but keep the
# SubIFD original — without this fallback shoot.py skipped them all.
EXIFREAD_DATE_TAGS = ('EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime')


def _read_photo_dt(path: Path) -> datetime.datetime | None:
    """Return best EXIF datetime for a photo file via exifread."""
    with path.open('rb') as f:
        tags = exifread.process_file(f)
    for key in EXIFREAD_DATE_TAGS:
        if key in tags:
            dt = parse_exif_dt(str(tags[key]))
            if dt is not None:
                return dt
    return None


def _read_video_dt(path: Path) -> datetime.datetime | None:
    """Return best capture datetime for a video file via exiftool.

    exifread only understands EXIF/TIFF-based metadata — it can't parse
    QuickTime/MP4 containers at all. Video goes through exiftool instead,
    using the same tag priority (EXIF_DATE_TAGS) organize.py uses, so a
    video tagged here gets the identical date when organize.py later
    re-derives it from the same file — the rename is idempotent, not a
    second distinct one.
    """
    tag_args = [f'-{t}' for t in EXIF_DATE_TAGS]
    try:
        proc = subprocess.run(
            ['exiftool', '-json', '-q', *tag_args, str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(proc.stdout) if proc.stdout.strip() else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    if not data:
        return None
    item = data[0]
    for key in EXIF_DATE_TAGS:
        if key in item:
            dt = parse_exif_dt(str(item[key]))
            if dt is not None:
                return dt
    return None


def _read_dt(path: Path) -> datetime.datetime | None:
    """Return best capture datetime for path, or None if none is parseable."""
    if path.suffix.lstrip('.').lower() in VIDEO_EXTS:
        return _read_video_dt(path)
    return _read_photo_dt(path)


def process_file(
    path: Path,
    tag: str,
    dry_run: bool = True,
    stem_suffix: str | None = None,
) -> None:
    if not path.is_file():
        print(f"Error: {path} is not a file.")
        return

    dt = _read_dt(path)
    if dt is None:
        print(f"Warning: No EXIF date found in {path.name}, skipping.")
        return

    rename_file(path, dt, tag, dry_run=dry_run, stem_suffix=stem_suffix)


def collect_media(directory: Path) -> list[Path]:
    """Return sorted photo/video files in directory (extension-filtered)."""
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lstrip('.').lower() in ALL_EXTS
    )


def rename_in_sequence(directory: Path, tag: str, dry_run: bool = False) -> int:
    """Rename all photos in directory using sequential numbering.

    Files are sorted by (capture_time, original_name) so ties (e.g. exports
    where every file shares one timestamp) are still deterministic.
    Width is the larger of 3 digits or what the count requires.
    Files without parseable EXIF dates are skipped with a warning.

    Returns the number of files renamed (or that would be in dry-run).
    """
    dated: list[tuple[Path, datetime.datetime]] = []
    for path in collect_media(directory):
        dt = _read_dt(path)
        if dt is None:
            print(f"Warning: No EXIF date found in {path.name}, skipping.")
            continue
        dated.append((path, dt))

    dated.sort(key=lambda pair: (pair[1], pair[0].name))
    width = max(3, len(str(len(dated))))
    for i, (path, dt) in enumerate(dated, start=1):
        rename_file(path, dt, tag, dry_run=dry_run, stem_suffix=f"{i:0{width}d}")
    return len(dated)


def check_already_renamed(directory: Path) -> list[str]:
    """Return names of any photo/video files that look like they've already been renamed."""
    return [
        p.name for p in directory.iterdir()
        if p.is_file()
        and p.suffix.lstrip('.').lower() in ALL_EXTS
        and ALREADY_RENAMED_RE.match(p.stem)
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Renames photo files to a date/time/tag convention."
    )
    parser.add_argument('directory', type=str, help="Directory to process.")
    parser.add_argument('tag', type=str, help="Tag to embed in filenames (e.g. 'liam06mo').")
    parser.add_argument('-x', '--execute', action='store_true',
                        help="Actually rename files (default is a dry-run preview).")
    parser.add_argument('--force', action='store_true',
                        help="Bypass the already-renamed check.")
    parser.add_argument('-n', '--sequence', action='store_true',
                        help="Number files sequentially (001, 002, …) instead of "
                             "preserving the original filename stem. Order is by "
                             "EXIF capture time, then original filename.")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    directory = Path(args.directory)
    tag = args.tag.lower()
    dry_run = not args.execute

    if not args.force:
        already_renamed = check_already_renamed(directory)
        if already_renamed:
            print("Warning: some files in this directory look like they've already been renamed:")
            for name in sorted(already_renamed):
                print(f"  {name}")
            print()
            if not dry_run:
                print("Aborting. Re-run with --force if you really want to proceed.")
                sys.exit(1)
            print("Dry run continuing — pass --force -x to execute anyway.\n")

    if dry_run:
        print("Dry run — no files will be changed. Pass -x to apply.\n")

    if args.sequence:
        rename_in_sequence(directory, tag=tag, dry_run=dry_run)
    else:
        for path in collect_media(directory):
            process_file(path, tag=tag, dry_run=dry_run)


if __name__ == '__main__':
    main()
