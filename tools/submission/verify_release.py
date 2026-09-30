"""One command: fresh PostgreSQL, tests, browser, validators and frozen archive."""

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.submission import build_release, verify_manifest


ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'dist'


def run(label, command, cwd, *, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = result.stdout.strip().splitlines()
    print(f'{label}: {"passed" if result.returncode == 0 else "FAILED"}')
    if result.returncode:
        print('\n'.join(lines[-18:]))
        raise RuntimeError(label)
    if lines:
        print(lines[-1])
    return result.stdout


def check_archive(tree):
    verify_manifest.verify(tree)
    manifest = json.loads((tree / 'MANIFEST.sha256.json').read_text(encoding='utf-8'))
    archive = tree.with_suffix('.zip')
    secrets = build_release.known_secrets()
    with zipfile.ZipFile(archive) as packed:
        assert packed.testzip() is None, 'ZIP CRC failed'
        names = set(packed.namelist())
        assert names == set(manifest) | {'MANIFEST.sha256.json', 'RELEASE.json'}
        for name in names:
            assert Path(name).name != '.env', 'Environment file in archive'
            data = packed.read(name)
            assert not any(value in data for value in secrets), 'Known secret in archive'
            assert not re.search(rb'^-----BEGIN .*PRIVATE KEY-----', data, re.M)
    print(f'ZIP CRC and secret scan: {len(names)} files passed')


def check_bundle_clone(tree):
    clone = (ROOT / '.tmp' / ('bundle-clone-' + uuid.uuid4().hex[:8])).resolve()
    assert clone.is_relative_to((ROOT / '.tmp').resolve())
    try:
        subprocess.run(['git', '-c', 'advice.detachedHead=false', 'clone', '--quiet',
                        '--branch', 'submission',
                        str(tree.with_suffix('.bundle')),
                        str(clone)], check=True)
        verify_manifest.verify(clone)
        head = subprocess.check_output(['git', '-C', str(clone), 'rev-parse', 'HEAD'], text=True).strip()
        source_head = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'], text=True).strip()
        assert head == source_head
        branch = subprocess.check_output(['git', '-C', str(clone), 'symbolic-ref', '--short', 'HEAD'], text=True).strip()
        assert branch == 'submission'
        assert not subprocess.check_output(['git', '-C', str(clone), 'status', '--porcelain'])
        print('Git bundle fresh clone: passed')
    finally:
        if clone.exists():
            assert clone.is_relative_to((ROOT / '.tmp').resolve())
            def writable_remove(function, path, _error):
                os.chmod(path, stat.S_IWRITE)
                function(path)
            shutil.rmtree(clone, onerror=writable_remove)


def main():
    source = json.loads((ROOT / 'submission/scenario.json').read_text(encoding='utf-8'))
    assert source['published_version'] == 10 and source['config']['rules']['child_only'] is True
    assert (ROOT / 'submission/.env').exists(), 'Copy submission/.env.example to submission/.env first'
    project = 'tochka-r5-' + uuid.uuid4().hex[:8]
    stage = (ROOT / '.tmp' / ('release-check-' + uuid.uuid4().hex[:8])).resolve()
    assert stage.is_relative_to((ROOT / '.tmp').resolve())
    tree = None
    compose = ['docker', 'compose', '-p', project, '--env-file', 'submission/.env',
               '-f', 'submission/compose.yaml']
    try:
        build_release.build(stage)
        shutil.copyfile(ROOT / 'submission/.env', stage / 'submission/.env')
        run('DATA-API validator', [sys.executable, 'tools/data-api/validate_data_api.py',
            'DATA-API.yaml', '--schema', 'tools/data-api/DATA-API.schema.json',
            '--openapi', 'openapi.yaml'], stage)
        run('OpenAPI validator', [sys.executable, '-m', 'openapi_spec_validator', 'openapi.yaml'], stage)
        run('Node dependencies', [shutil.which('npm') or 'npm', 'ci', '--ignore-scripts'], stage)
        run('Playwright Chromium', [shutil.which('npx') or 'npx', 'playwright', 'install', 'chromium'], stage)
        run('Fresh PostgreSQL and preview', [*compose, 'up', '-d', '--build', 'miniapp'], stage)
        tests = run('Full Python suite', [*compose, 'run', '--rm', '--no-deps',
            '-e', 'HELP_API_URL=', '-e', 'HELP_CITY_FIRST=', '-e', 'TRUDVSEM_CACHE_PATH=',
            '-e', 'REEF_CACHE_PATH=', 'miniapp', 'python', '-m', 'pytest', '-q',
            '-p', 'no:cacheprovider', 'project/llm/tests', 'project/admin/tests',
            'project/miniapp/tests', 'tools/submission/test_backup_retention.py',
            '--basetemp=/tmp/r5-release-tests'], stage)
        summary = re.search(r'(\d+) passed(?:[^\n]*)', tests)
        assert summary and not re.search(r'\b(?:failed|skipped|xfailed|xpassed)\b', summary.group()), summary
        run('Node suite', [shutil.which('npm') or 'npm', 'test'], stage)
        run('Action retry on PostgreSQL', [sys.executable, 'tools/submission/check_action_idempotency.py'], stage)
        run('Second preview instance', [*compose, 'run', '-d', '--no-deps',
            '-p', '127.0.0.1:18867:8766', 'miniapp'], stage)
        run('Action retry across instances', [sys.executable, 'tools/submission/check_action_idempotency.py',
            '--second', 'http://127.0.0.1:18867'], stage)
        run('Action retry after restart', [sys.executable, 'tools/submission/check_action_restart.py',
            '--project', project], stage)
        time.sleep(5)
        browser_env = dict(os.environ, PLAYWRIGHT_HEADLESS='1')
        run('Browser preview regression', [shutil.which('npm') or 'npm', 'run', 'test:browser'], stage, env=browser_env)
    finally:
        subprocess.run([*compose, 'down', '-v', '--remove-orphans'], cwd=stage if stage.exists() else ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if stage.exists():
            assert stage.is_relative_to((ROOT / '.tmp').resolve())
            shutil.rmtree(stage)
    tree = DIST / ('tochka-opory-r5-verified-' + uuid.uuid4().hex[:8])
    build_release.build(tree)
    build_release.freeze(tree)
    check_archive(tree)
    check_bundle_clone(tree)
    print(f'RELEASE={tree}')
    print(f'PYTHON={summary.group()}')


if __name__ == '__main__':
    main()
