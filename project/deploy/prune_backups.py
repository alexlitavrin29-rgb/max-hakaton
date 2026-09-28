"""Expire database and configuration backups after 30 days."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil


BACKUP_ROOT = Path('/opt/tochka/backups')
RETENTION = timedelta(days=30)
ARCHIVE_SUFFIXES = ('.dump', '.tar.gz')


def _is_archive(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.name.endswith(ARCHIVE_SUFFIXES)


def expired_backups(root: Path, now: datetime):
    """Select only direct backup items; never follow symlinks."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Backup root must be a real directory')
    cutoff = (now - RETENTION).timestamp()
    for path in root.iterdir():
        if path.is_symlink():
            continue
        if path.name == 'managed' and path.is_dir():
            for child in path.iterdir():
                if child.is_symlink() or not child.is_dir() or not (child / '.managed-backup').is_file():
                    continue
                if not re.fullmatch(r'\d{8}T\d{6}Z', child.name):
                    continue
                created = datetime.strptime(child.name, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
                if created <= now - RETENTION:
                    yield child
        elif _is_archive(path) and path.stat().st_mtime <= cutoff:
            yield path
        elif path.is_dir() and any(_is_archive(child) for child in path.iterdir()):
            if path.stat().st_mtime <= cutoff:
                yield path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Delete expired backups')
    args = parser.parse_args()
    targets = list(expired_backups(BACKUP_ROOT, datetime.now(timezone.utc)))
    if args.apply:
        for path in targets:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    print(f'expired={len(targets)} removed={len(targets) if args.apply else 0}')


if __name__ == '__main__':
    main()
