"""Create a clean review tree from an allowlist; keep working history untouched."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SKIP = {'__pycache__', '.pytest_cache', '.testdeps', '.git', 'cache', 'logs', 'backups', 'backup', 'node_modules'}
HISTORICAL_EXECUTABLES = {'historical_repair_dialogue_replay.py', 'replay_current_v1.json'}
SUBMISSION_DOCS = {'ACCESS.md', 'DATA-POLICY.md', 'MISSING.md', 'PRODUCT.md',
                   'TECHNICAL.md', 'VERIFICATION.md', 'LOAD.md', 'HOUSING-2026-09-30.md',
                   'load-results.json', 'load-monitor-summary.json'}
SUFFIXES = {'.py', '.json', '.jsonl', '.csv', '.md', '.txt', '.yaml', '.yml',
            '.html', '.css', '.js', '.cjs', '.svg', '.crt', '.gz', '.sh', '.service', '.timer', '.pdf', '.pptx'}
FIXTURES = [
    'project/logs/.gitkeep',
    'docs/research/help-points/NEW_ORGANIZATIONS_2026-09-25.csv',
    'docs/research/help-points/POINT_AUDIT_2026-09-25.csv',
    'docs/research/help-points/LINK_AUDIT_2026-09-25.csv',
    'docs/test-results/quality-2026-09-26/scenario.json',
    'docs/test-results/warm-child-2026-09-27/proposed.json',
    'docs/test-results/warm-child-2026-09-27/server-before.json',
    'docs/test-results/quality-independent-2026-09-26/independent-review-private/repair-packet-private.json',
    'docs/test-results/quality-independent-2026-09-26/baseline-initial/scenario.json',
    'docs/test-results/quality-independent-2026-09-26/baseline-initial/baseline.json',
    'docs/quality-acceptance/capture_control.py',
    'docs/quality-acceptance/evaluate.py',
    'docs/quality-acceptance/execution_guard.py',
    'docs/quality-acceptance/open-neighbors-after-control.json',
    'docs/quality-acceptance/sealed-2026-09-26/geography.json',
    'docs/quality-acceptance/sealed-2026-09-26/housing.jsonl',
    'docs/back-navigation-2026-09-27/update.py',
]


def files():
    selected = {ROOT / name for name in FIXTURES}
    for folder in ['project/llm', 'project/admin', 'project/miniapp', 'project/deploy', 'project/data/knowledge',
                   'submission', 'tools/data-api', 'tools/submission', 'docs/submission']:
        for path in (ROOT / folder).rglob('*'):
            if any(part in SKIP or part.startswith('.tmp') for part in path.relative_to(ROOT).parts):
                continue
            if folder == 'docs/submission' and path.name not in SUBMISSION_DOCS:
                continue
            if path.is_file() and path.name not in HISTORICAL_EXECUTABLES and (path.suffix in SUFFIXES or path.name.startswith('Dockerfile') or path.name in {'Caddyfile', '.env.example'}):
                selected.add(path)
    selected.update(ROOT / name for name in ('README.md', 'DATA-API.yaml', 'openapi.yaml',
        'requirements-dev.txt', 'requirements-validation.txt', 'package.json', 'package-lock.json',
        '.gitignore', '.gitattributes', '.dockerignore', '.env.example'))
    return sorted(selected)


def known_secrets():
    # Compare known local secrets without printing them or copying the env file.
    secrets = []
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            name, sep, value = line.partition('=')
            value = value.strip().strip('\"\'')
            if sep and any(word in name.upper() for word in ('TOKEN', 'KEY', 'PASSWORD', 'SECRET')) and len(value) >= 12:
                secrets.append(value.encode())
    return secrets


def build(destination):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Destination already exists; choose a new version directory')
    selected, secrets = files(), known_secrets()
    # Fail before creating a partial tree if a required fixture or known secret is found.
    for path in selected:
        data = path.read_bytes()
        if any(value in data for value in secrets) or re.search(rb'^-----BEGIN .*PRIVATE KEY-----', data, re.M):
            raise ValueError('Potential secret in ' + path.relative_to(ROOT).as_posix())
    destination.mkdir(parents=True)
    for path in selected:
        target = destination / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    manifest = {path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(destination.rglob('*')) if path.is_file()}
    (destination / 'MANIFEST.sha256.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (destination / 'RELEASE.json').write_text(json.dumps(dict(version='submission-2026-09-30-housing-v10',
        scenario_version=json.loads((destination / 'submission/scenario.json').read_text(encoding='utf-8-sig'))['published_version'], file_count=len(manifest), manifest_sha256=hashlib.sha256(
            (destination / 'MANIFEST.sha256.json').read_bytes()).hexdigest()), indent=2) + '\n', encoding='utf-8')
    return destination


def freeze(destination):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(destination), *args], text=True).strip()
    git('init', '-b', 'submission')
    git('config', 'core.autocrlf', 'false')
    git('add', '.')
    git('-c', 'user.name=Submission snapshot', '-c', 'user.email=submission@localhost',
        'commit', '-m', 'Freeze Tochka Opory verified r5 release 2026-09-28')
    revision = git('rev-parse', 'HEAD')
    git('bundle', 'create', str(destination.parent / (destination.name + '.bundle')),
        'refs/heads/submission')
    archive = destination.parent / (destination.name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(destination.rglob('*')):
            if path.is_file() and '.git' not in path.relative_to(destination).parts:
                output.write(path, path.relative_to(destination).as_posix())
    receipt = dict(git_commit=revision, archive=archive.name,
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        working_tree_clean=not bool(git('status', '--porcelain')))
    (destination.parent / (destination.name + '.receipt.json')).write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--freeze', action='store_true', help='Create local Git snapshot, bundle and ZIP')
    args = parser.parse_args()
    destination = build(args.destination)
    if args.freeze:
        freeze(destination)
    else:
        print('Clean tree created:', destination)
