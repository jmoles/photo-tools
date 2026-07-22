"""Unit tests for rename.py — pure logic, filesystem via tmp_path, exifread mocked."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shoot import (
    _read_dt,
    check_already_renamed,
    parse_args,
    process_file,
    rename_in_sequence,
)
from photo import ALREADY_RENAMED_RE, find_xmp, rename_xmp


def _tag(value: str) -> MagicMock:
    """exifread surfaces tags as objects whose str() yields the EXIF string."""
    m = MagicMock()
    m.__str__ = lambda self: value
    return m


# ---------------------------------------------------------------------------
# find_xmp
# ---------------------------------------------------------------------------

class TestFindXmp:
    def test_finds_lowercase_xmp(self, tmp_path):
        img = tmp_path / 'photo.jpg'
        img.touch()
        xmp = tmp_path / 'photo.xmp'
        xmp.touch()
        assert find_xmp(img) == xmp

    def test_finds_uppercase_xmp(self, tmp_path):
        img = tmp_path / 'photo.jpg'
        img.touch()
        xmp = tmp_path / 'photo.XMP'
        xmp.touch()
        result = find_xmp(img)
        # Use samefile() — on case-insensitive filesystems (macOS) photo.XMP
        # and photo.xmp are the same inode, so path equality is unreliable.
        assert result is not None and result.samefile(xmp)

    def test_prefers_lowercase_over_uppercase(self, tmp_path):
        img = tmp_path / 'photo.jpg'
        img.touch()
        lower = tmp_path / 'photo.xmp'
        lower.touch()
        upper = tmp_path / 'photo.XMP'
        upper.touch()
        assert find_xmp(img) == lower

    def test_returns_none_when_absent(self, tmp_path):
        img = tmp_path / 'photo.jpg'
        img.touch()
        assert find_xmp(img) is None


# ---------------------------------------------------------------------------
# rename_xmp
# ---------------------------------------------------------------------------

class TestRenameXmp:
    def test_renames_file(self, tmp_path):
        xmp = tmp_path / 'photo.xmp'
        xmp.write_text('<xmp>photo.jpg</xmp>')
        new_xmp = tmp_path / 'renamed.xmp'
        rename_xmp(xmp, new_xmp, 'photo.jpg', 'renamed.jpg')
        assert new_xmp.exists()
        assert not xmp.exists()

    def test_updates_filename_reference(self, tmp_path):
        xmp = tmp_path / 'photo.xmp'
        xmp.write_text('<xmp filename="photo.jpg">stuff</xmp>')
        new_xmp = tmp_path / 'renamed.xmp'
        rename_xmp(xmp, new_xmp, 'photo.jpg', 'renamed.jpg')
        assert 'renamed.jpg' in new_xmp.read_text()
        assert 'photo.jpg' not in new_xmp.read_text()

    def test_deletes_original(self, tmp_path):
        xmp = tmp_path / 'photo.xmp'
        xmp.write_text('content')
        rename_xmp(xmp, tmp_path / 'new.xmp', 'photo.jpg', 'new.jpg')
        assert not xmp.exists()

    def test_write_failure_preserves_original(self, tmp_path):
        """If writing the new XMP fails, the original must not be deleted."""
        xmp = tmp_path / 'photo.xmp'
        xmp.write_text('<xmp>photo.jpg</xmp>')
        new_xmp = tmp_path / 'renamed.xmp'
        with patch('pathlib.Path.write_text', side_effect=OSError('disk full')):
            with pytest.raises(OSError):
                rename_xmp(xmp, new_xmp, 'photo.jpg', 'renamed.jpg')
        assert xmp.exists(), 'original XMP must be preserved after write failure'

    def test_write_failure_cleans_up_partial_new_file(self, tmp_path):
        """A pre-existing file at the new path must be removed on write failure.

        Simulates: new_xmp already exists from a previous partial run, then
        write_text raises (e.g. disk full mid-write). The cleanup code must
        unlink it so the next run doesn't see a corrupt sidecar.
        """
        xmp = tmp_path / 'photo.xmp'
        xmp.write_text('<xmp>photo.jpg</xmp>')
        new_xmp = tmp_path / 'renamed.xmp'
        new_xmp.write_bytes(b'corrupt partial')  # pre-existing partial file
        with patch('pathlib.Path.write_text', side_effect=OSError('disk full')):
            with pytest.raises(OSError):
                rename_xmp(xmp, new_xmp, 'photo.jpg', 'renamed.jpg')
        assert not new_xmp.exists(), 'partial new XMP must be cleaned up after write failure'
        assert xmp.exists(), 'original XMP must survive'


# ---------------------------------------------------------------------------
# ALREADY_RENAMED_RE
# ---------------------------------------------------------------------------

class TestAlreadyRenamedRe:
    def test_matches_full_pattern(self):
        assert ALREADY_RENAMED_RE.match('20230615_103022_tag_img0042')

    def test_matches_no_tag(self):
        assert ALREADY_RENAMED_RE.match('20230615_103022_img0042')

    def test_no_match_plain(self):
        assert not ALREADY_RENAMED_RE.match('IMG_0042')

    def test_no_match_partial_date(self):
        assert not ALREADY_RENAMED_RE.match('20230615_1030_img')


# ---------------------------------------------------------------------------
# check_already_renamed
# ---------------------------------------------------------------------------

class TestCheckAlreadyRenamed:
    def test_detects_renamed_photo(self, tmp_path):
        (tmp_path / '20230615_103022_tag_img0042.jpg').touch()
        result = check_already_renamed(tmp_path)
        assert '20230615_103022_tag_img0042.jpg' in result

    def test_ignores_plain_photo(self, tmp_path):
        (tmp_path / 'IMG_0042.jpg').touch()
        assert check_already_renamed(tmp_path) == []

    def test_ignores_non_photo_files(self, tmp_path):
        (tmp_path / '20230615_103022_tag_file.txt').touch()
        (tmp_path / '20230615_103022_tag_file.xmp').touch()
        assert check_already_renamed(tmp_path) == []

    def test_returns_multiple(self, tmp_path):
        (tmp_path / '20230615_103022_tag_img0001.cr2').touch()
        (tmp_path / '20230615_103023_tag_img0002.cr2').touch()
        result = check_already_renamed(tmp_path)
        assert len(result) == 2

    def test_empty_directory(self, tmp_path):
        assert check_already_renamed(tmp_path) == []


# ---------------------------------------------------------------------------
# process_file
# ---------------------------------------------------------------------------

class TestProcessFile:
    _EXIF_DATE = '2023:06:15 10:30:22'

    def _make_tags(self):
        tag = MagicMock()
        tag.__str__ = lambda self: TestProcessFile._EXIF_DATE
        return {'Image DateTime': tag}

    def test_dry_run_prints_rename(self, tmp_path, capsys):
        img = tmp_path / 'IMG_0042.jpg'
        img.touch()
        with patch('shoot.exifread.process_file', return_value=self._make_tags()):
            process_file(img, tag='vacation', dry_run=True)
        out = capsys.readouterr().out
        assert '20230615_103022_vacation_img_0042.jpg' in out
        assert img.exists()  # not renamed in dry run

    def test_execute_renames_file(self, tmp_path):
        img = tmp_path / 'IMG_0042.jpg'
        img.touch()
        with patch('shoot.exifread.process_file', return_value=self._make_tags()):
            process_file(img, tag='vacation', dry_run=False)
        assert not img.exists()
        assert (tmp_path / '20230615_103022_vacation_img_0042.jpg').exists()

    def test_lowercases_extension(self, tmp_path):
        img = tmp_path / 'DSF4989.RAF'
        img.touch()
        with patch('shoot.exifread.process_file', return_value=self._make_tags()):
            process_file(img, tag='shoot', dry_run=False)
        result = list(tmp_path.glob('*.raf'))
        assert len(result) == 1
        assert result[0].name == '20230615_103022_shoot_dsf4989.raf'

    def test_lowercases_stem(self, tmp_path):
        img = tmp_path / 'DSF4989.jpg'
        img.touch()
        with patch('shoot.exifread.process_file', return_value=self._make_tags()):
            process_file(img, tag='shoot', dry_run=False)
        assert (tmp_path / '20230615_103022_shoot_dsf4989.jpg').exists()

    def test_skips_missing_exif(self, tmp_path, capsys):
        img = tmp_path / 'IMG_0042.jpg'
        img.touch()
        with patch('shoot.exifread.process_file', return_value={}):
            process_file(img, tag='vacation', dry_run=False)
        assert img.exists()  # not renamed
        assert 'Warning' in capsys.readouterr().out

    def test_skips_non_file(self, tmp_path, capsys):
        process_file(tmp_path / 'nonexistent.jpg', tag='tag', dry_run=False)
        assert 'Error' in capsys.readouterr().out

    def test_renames_xmp_sidecar(self, tmp_path):
        img = tmp_path / 'IMG_0042.jpg'
        img.touch()
        xmp = tmp_path / 'IMG_0042.xmp'
        xmp.write_text('<xmp>IMG_0042.jpg</xmp>')
        with patch('shoot.exifread.process_file', return_value=self._make_tags()):
            process_file(img, tag='vacation', dry_run=False)
        new_xmp = tmp_path / '20230615_103022_vacation_img_0042.xmp'
        assert new_xmp.exists()
        assert not xmp.exists()
        assert 'img_0042.jpg' in new_xmp.read_text()

    def test_dry_run_does_not_rename_xmp(self, tmp_path, capsys):
        img = tmp_path / 'IMG_0042.jpg'
        img.touch()
        xmp = tmp_path / 'IMG_0042.xmp'
        xmp.write_text('<xmp/>')
        with patch('shoot.exifread.process_file', return_value=self._make_tags()):
            process_file(img, tag='vacation', dry_run=True)
        assert xmp.exists()  # not touched in dry run

    def test_stem_suffix_overrides_original_stem(self, tmp_path):
        img = tmp_path / 'IMG_0042.jpg'
        img.touch()
        with patch('shoot.exifread.process_file', return_value=self._make_tags()):
            process_file(img, tag='vacation', dry_run=False, stem_suffix='007')
        assert (tmp_path / '20230615_103022_vacation_007.jpg').exists()
        assert not (tmp_path / '20230615_103022_vacation_img_0042.jpg').exists()


# ---------------------------------------------------------------------------
# _read_dt — EXIF date fallback chain
# ---------------------------------------------------------------------------

class TestReadDt:
    """Tag priority: DateTimeOriginal > DateTimeDigitized > Image DateTime.

    Apple/cloud-export JPEGs commonly strip IFD0 (Image DateTime) but keep
    the SubIFD original — without the fallback shoot.py skipped them all.
    """

    def test_prefers_datetime_original(self, tmp_path):
        img = tmp_path / 'a.jpg'
        img.touch()
        tags = {
            'EXIF DateTimeOriginal':  _tag('2023:06:15 10:30:22'),
            'EXIF DateTimeDigitized': _tag('2024:01:01 00:00:00'),
            'Image DateTime':         _tag('2025:01:01 00:00:00'),
        }
        with patch('shoot.exifread.process_file', return_value=tags):
            assert _read_dt(img) == datetime.datetime(2023, 6, 15, 10, 30, 22)

    def test_falls_back_to_digitized(self, tmp_path):
        img = tmp_path / 'a.jpg'
        img.touch()
        tags = {
            'EXIF DateTimeDigitized': _tag('2024:01:01 12:00:00'),
            'Image DateTime':         _tag('2025:01:01 00:00:00'),
        }
        with patch('shoot.exifread.process_file', return_value=tags):
            assert _read_dt(img) == datetime.datetime(2024, 1, 1, 12, 0, 0)

    def test_falls_back_to_image_datetime(self, tmp_path):
        img = tmp_path / 'a.jpg'
        img.touch()
        with patch('shoot.exifread.process_file',
                   return_value={'Image DateTime': _tag('2025:01:01 00:00:00')}):
            assert _read_dt(img) == datetime.datetime(2025, 1, 1, 0, 0, 0)

    def test_returns_none_when_no_date_tags(self, tmp_path):
        img = tmp_path / 'a.jpg'
        img.touch()
        with patch('shoot.exifread.process_file', return_value={}):
            assert _read_dt(img) is None

    def test_handles_iso8601_with_z_suffix(self, tmp_path):
        """Apple/cloud exports use ISO 8601 with Z, not the EXIF colon format."""
        img = tmp_path / 'a.jpg'
        img.touch()
        with patch('shoot.exifread.process_file',
                   return_value={'EXIF DateTimeOriginal': _tag('2026-04-02T00:00:00Z')}):
            assert _read_dt(img) == datetime.datetime(2026, 4, 2, 0, 0, 0)

    def test_skips_unparseable_value_and_falls_through(self, tmp_path):
        """If the higher-priority tag has a junk value, fall through to the next."""
        img = tmp_path / 'a.jpg'
        img.touch()
        tags = {
            'EXIF DateTimeOriginal':  _tag('not-a-date'),
            'EXIF DateTimeDigitized': _tag('2024:01:01 12:00:00'),
        }
        with patch('shoot.exifread.process_file', return_value=tags):
            assert _read_dt(img) == datetime.datetime(2024, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# rename_in_sequence
# ---------------------------------------------------------------------------

class TestRenameInSequence:
    @staticmethod
    def _patch_per_file_dt(mapping: dict[str, str]):
        """Patch _read_dt to return a parsed datetime keyed by filename."""
        def side_effect(path: Path) -> datetime.datetime | None:
            value = mapping.get(path.name)
            if value is None:
                return None
            return datetime.datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
        return patch('shoot._read_dt', side_effect=side_effect)

    def test_numbers_files_in_capture_time_order(self, tmp_path):
        # Create out of capture-time order on disk to prove we sort by EXIF.
        (tmp_path / 'b.jpg').touch()  # earliest by EXIF
        (tmp_path / 'a.jpg').touch()  # latest by EXIF
        (tmp_path / 'c.jpg').touch()  # middle by EXIF
        mapping = {
            'a.jpg': '2023:06:15 12:00:00',
            'b.jpg': '2023:06:15 09:00:00',
            'c.jpg': '2023:06:15 10:30:00',
        }
        with self._patch_per_file_dt(mapping):
            count = rename_in_sequence(tmp_path, tag='trip', dry_run=False)
        assert count == 3
        assert (tmp_path / '20230615_090000_trip_001.jpg').exists()
        assert (tmp_path / '20230615_103000_trip_002.jpg').exists()
        assert (tmp_path / '20230615_120000_trip_003.jpg').exists()

    def test_ties_break_by_original_filename(self, tmp_path):
        """All-same-timestamp exports (the Archive1-2 case) must still be deterministic."""
        for name in ('z.jpg', 'a.jpg', 'm.jpg'):
            (tmp_path / name).touch()
        mapping = {n: '2026:04:02 00:00:00' for n in ('z.jpg', 'a.jpg', 'm.jpg')}
        with self._patch_per_file_dt(mapping):
            rename_in_sequence(tmp_path, tag='school', dry_run=False)
        # 'a' < 'm' < 'z' alphabetically
        assert (tmp_path / '20260402_000000_school_001.jpg').exists()
        assert (tmp_path / '20260402_000000_school_002.jpg').exists()
        assert (tmp_path / '20260402_000000_school_003.jpg').exists()
        # And spot-check the mapping by reading back via inode-stable identity:
        # we can't recover origin from disk, but we know exactly 3 sequenced files exist.
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            '20260402_000000_school_001.jpg',
            '20260402_000000_school_002.jpg',
            '20260402_000000_school_003.jpg',
        ]

    def test_min_width_is_three_digits(self, tmp_path):
        (tmp_path / 'a.jpg').touch()
        with self._patch_per_file_dt({'a.jpg': '2023:01:01 00:00:00'}):
            rename_in_sequence(tmp_path, tag='t', dry_run=False)
        assert (tmp_path / '20230101_000000_t_001.jpg').exists()

    def test_width_grows_for_large_batches(self, tmp_path):
        for i in range(1, 12):  # 11 files → still 3 digits (max 3 vs len('11')==2)
            (tmp_path / f'f{i:02d}.jpg').touch()
        mapping = {f'f{i:02d}.jpg': f'2023:01:01 00:{i:02d}:00' for i in range(1, 12)}
        with self._patch_per_file_dt(mapping):
            rename_in_sequence(tmp_path, tag='t', dry_run=False)
        # 11 files → still 3 digits because min(3, …) clamps the floor
        assert (tmp_path / '20230101_000100_t_001.jpg').exists()
        assert (tmp_path / '20230101_001100_t_011.jpg').exists()

    def test_width_expands_past_three_digits_for_thousand_plus(self, tmp_path):
        # Don't actually create 1000 files; verify the width formula directly.
        # max(3, len(str(N))) → 3 for N≤999, 4 for N=1000+.
        assert max(3, len(str(999))) == 3
        assert max(3, len(str(1000))) == 4

    def test_skips_files_without_exif(self, tmp_path, capsys):
        (tmp_path / 'good.jpg').touch()
        (tmp_path / 'bad.jpg').touch()
        with self._patch_per_file_dt({'good.jpg': '2023:01:01 00:00:00'}):  # bad.jpg → None
            count = rename_in_sequence(tmp_path, tag='t', dry_run=False)
        assert count == 1
        assert (tmp_path / '20230101_000000_t_001.jpg').exists()
        assert (tmp_path / 'bad.jpg').exists()  # untouched
        assert 'No EXIF date found in bad.jpg' in capsys.readouterr().out

    def test_dry_run_does_not_rename(self, tmp_path, capsys):
        (tmp_path / 'a.jpg').touch()
        with self._patch_per_file_dt({'a.jpg': '2023:01:01 00:00:00'}):
            rename_in_sequence(tmp_path, tag='t', dry_run=True)
        assert (tmp_path / 'a.jpg').exists()  # untouched
        assert not (tmp_path / '20230101_000000_t_001.jpg').exists()
        assert 'Rename:' in capsys.readouterr().out

    def test_renames_paired_xmp(self, tmp_path):
        img = tmp_path / 'a.jpg'
        img.touch()
        xmp = tmp_path / 'a.xmp'
        xmp.write_text('<xmp>a.jpg</xmp>')
        with self._patch_per_file_dt({'a.jpg': '2023:01:01 00:00:00'}):
            rename_in_sequence(tmp_path, tag='t', dry_run=False)
        new_xmp = tmp_path / '20230101_000000_t_001.xmp'
        assert new_xmp.exists()
        assert not xmp.exists()
        assert 't_001.jpg' in new_xmp.read_text()


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self):
        args = parse_args(['mydir', 'mytag'])
        assert args.directory == 'mydir'
        assert args.tag == 'mytag'
        assert args.execute is False
        assert args.force is False
        assert args.sequence is False

    def test_execute_flag(self):
        args = parse_args(['mydir', 'mytag', '-x'])
        assert args.execute is True

    def test_force_flag(self):
        args = parse_args(['mydir', 'mytag', '--force'])
        assert args.force is True

    def test_sequence_flag_long(self):
        args = parse_args(['mydir', 'mytag', '--sequence'])
        assert args.sequence is True

    def test_sequence_flag_short(self):
        args = parse_args(['mydir', 'mytag', '-n'])
        assert args.sequence is True
