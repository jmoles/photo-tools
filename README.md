# photo-tools

A Python toolkit for renaming and consolidating photo libraries using EXIF metadata.

## Scripts

### `ingest.py` — SD card import

Auto-detects a mounted SD card, reads EXIF, clusters files into shoots by time gap, and copies them to a local staging directory. Tracks imports in a SQLite database so re-running skips already-imported files.

```sh
# Preview (dry-run, default)
uv run ingest.py

# Apply
uv run ingest.py --execute

# Apply with a default tag suggestion per shoot
uv run ingest.py --tag tokyo --execute
```

Per shoot you are prompted for a tag, or `s` to skip, or `i` to ignore the shoot forever.

---

### `shoot.py` — single-shoot rename

Renames all photos in a directory to a consistent date/time convention using embedded EXIF data. Designed for processing one shoot at a time.

```sh
# Preview (dry-run, default)
uv run shoot.py /path/to/photos tagname

# Apply
uv run shoot.py /path/to/photos tagname -x
```

**Output format:**

```
{date}_{time}_{tag}_{original}.{ext}
```

| Part | Example | Description |
|------|---------|-------------|
| `date` | `20260118` | EXIF capture date (YYYYMMDD) |
| `time` | `152258` | EXIF capture time (HHMMSS) |
| `tag` | `liam06mo` | User-supplied label for the shoot |
| `original` | `dsf4768` | Original filename stem, lowercased |

Example: `DSF4768.RAF` → `20260118_152258_liam06mo_dsf4768.raf`

XMP sidecar files are renamed to match and their internal filename references are updated automatically.

---

### `organize.py` — bulk library consolidation

Recursively processes a large photo library: renames by EXIF date, deduplicates by content hash, organises into `YYYY/MM` folders, and moves sidecars alongside their originals. Designed for one-time or incremental consolidation of an unorganised archive.

```sh
# Preview (dry-run, default)
uv run organize.py

# Apply
uv run organize.py --execute

# Override config file paths
uv run organize.py --source /path/to/unorg --dest /path/to/library --execute
```

**Configuration:** copy `config.example.toml` to `config.toml` and fill in your paths. `config.toml` is gitignored.

**Output layout:**

```
<dest>/
  YYYY/MM/          ← organised photos
  _undated/         ← files dated by mtime only (flagged _mtime in filename)
  dupes/YYYY/MM/    ← hash duplicates (never deleted)
  movies/YYYY/MM/   ← standalone video files
  _review/          ← PSDs, Lightroom catalogs, unpaired thumbnails
```

**Date extraction fallback chain:**

1. Embedded EXIF (`DateTimeOriginal` → `CreateDate` → `ModifyDate`)
2. Paired XMP sidecar EXIF
3. Date regex parsed from filename — `_fndate` appended to output filename
4. Filesystem mtime — `_mtime` appended, file goes to `_undated/`

**Resume support:** a SQLite cache (`consolidate_cache.db`) tracks processed files using a fast size+mtime fingerprint. Interrupted runs resume without re-hashing completed files.

---

### `dedup.py` — deep duplicate scan

Compares a source directory against a library using two-stage detection: SHA-256 of raw bytes (fast, catches exact copies) then SHA-256 of decoded pixels (catches EXIF-stripped copies). Duplicates are moved to a `dupes/` directory, never deleted.

```sh
# Preview (dry-run, default)
uv run dedup.py --source /path/to/scan --library /path/to/library

# Apply
uv run dedup.py --source /path/to/scan --library /path/to/library --execute
```

Requires [Pillow](https://python-pillow.org/) for pixel decoding (installed automatically by `uv run`).

---

## Supported formats

| Category | Extensions |
|----------|-----------|
| Photos | CR2, CR3, RAF, DNG, HEIC, JPG, JPEG, TIF, TIFF, PNG, WEBP, BMP |
| Video | MOV, MP4, M4V, MPG, MPEG, AVI, WMV |
| Sidecars | XMP, PP3 (both travel with their paired original) |
| Live Photos | MOV paired with HEIC by filename stem |

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `exiftool` system binary

```sh
# macOS
brew install exiftool

# Fedora / RHEL
sudo dnf install perl-Image-ExifTool

# Debian / Ubuntu
sudo apt install libimage-exiftool-perl
```

## Tests

```sh
uv run --group dev pytest tests/                     # all tests
uv run --group dev pytest tests/test_unit.py         # unit only (no exiftool needed)
uv run --group dev pytest tests/test_integration.py  # requires exiftool + git lfs pull
```

## License

Code: [MIT](LICENSE)
Sample files in `samples/`: [CC BY 4.0](LICENSE-samples)
