"""Check every submitted file against the release manifest from the current root."""
import hashlib
import json
from pathlib import Path


def verify(root):
    manifest_path = root / 'MANIFEST.sha256.json'
    release = json.loads((root / 'RELEASE.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == release['manifest_sha256'], 'Manifest changed'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for name, digest in manifest.items():
        path = (root / name).resolve()
        assert path.is_relative_to(root.resolve()), 'Path outside release'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Changed file: ' + name
    print('Verified files:', len(manifest))


if __name__ == '__main__':
    verify(Path.cwd())
