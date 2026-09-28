"""Regression checks for the VPS backup retention rule."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from unittest.mock import patch

from project.deploy.prune_backups import expired_backups


def test_only_old_backup_snapshots_are_selected(tmp_path: Path):
    managed = tmp_path / 'managed'
    managed.mkdir()
    old_managed = managed / '20260801T030000Z'
    old_managed.mkdir()
    (old_managed / '.managed-backup').touch()
    (old_managed / 'database.dump').touch()
    fresh_managed = managed / '20260927T030000Z'
    fresh_managed.mkdir()
    (fresh_managed / '.managed-backup').touch()

    old_dump = tmp_path / 'database-20260801T030000Z.dump'
    old_dump.touch()
    old_archive = tmp_path / 'before-release-20260801.tar.gz'
    old_archive.touch()
    old_directory = tmp_path / 'r5-before-old'
    old_directory.mkdir()
    (old_directory / 'database.dump').touch()
    unrelated_directory = tmp_path / 'notes'
    unrelated_directory.mkdir()
    (unrelated_directory / 'readme.txt').touch()
    script = tmp_path / 'backup_managed.sh'
    script.touch()
    old_time = (datetime(2026, 8, 1, tzinfo=timezone.utc)).timestamp()
    for path in (old_dump, old_archive, old_directory, unrelated_directory, script):
        os.utime(path, (old_time, old_time))

    selected = set(expired_backups(tmp_path, datetime(2026, 9, 28, tzinfo=timezone.utc)))
    assert selected == {old_managed, old_dump, old_archive, old_directory}


def test_symlinks_and_unmarked_managed_directories_are_not_selected(tmp_path: Path):
    managed = tmp_path / 'managed'
    managed.mkdir()
    unmarked = managed / '20260801T030000Z'
    unmarked.mkdir()
    outside = tmp_path.parent / 'outside-backup.dump'
    outside.touch()
    link = tmp_path / 'linked.dump'
    link.touch()
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    old_time = (datetime(2026, 8, 1, tzinfo=timezone.utc)).timestamp()
    os.utime(link, (old_time, old_time))
    original = Path.is_symlink
    with patch.object(Path, 'is_symlink', lambda path: path == link or original(path)):
        assert list(expired_backups(tmp_path, now)) == []


def test_retention_boundary_is_thirty_days(tmp_path: Path):
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    archive = tmp_path / 'config.tar.gz'
    archive.touch()
    cutoff = (now - timedelta(days=30)).timestamp()
    os.utime(archive, (cutoff, cutoff))
    assert list(expired_backups(tmp_path, now)) == [archive]
