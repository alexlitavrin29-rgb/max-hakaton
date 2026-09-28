"""Run on the VPS once. Store generated editor credentials in root-only files."""
import os
from pathlib import Path
import secrets
import subprocess

root = Path('/opt/tochka')
os.umask(0o077)
credentials = root / '.deploy-secrets.env'
if not credentials.exists():
    password = secrets.token_urlsafe(24)
    result = subprocess.run(['docker', 'run', '--rm', '-i', 'caddy:2-alpine',
                             'caddy', 'hash-password'], input=password + '\n',
                            text=True, capture_output=True, check=True)
    hashed = next(line for line in result.stdout.splitlines() if line.startswith('$2'))
    credentials.write_text("ADMIN_PASSWORD_HASH='" + hashed + "'\n")
    (root / 'admin-access.txt').write_text(
        'https://admin.135-106-229-210.sslip.io/\nLogin: owner\nPassword: ' + password + '\n')
    print('Editor credentials generated (root-only file).')
else:
    print('Existing editor credentials preserved.')
