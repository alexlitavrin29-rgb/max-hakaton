"""VPS-side numeric Docker and mini-app queue monitor for a bounded load run."""

import argparse
import json
import subprocess
import time
from pathlib import Path


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    return result.stdout.strip() if result.returncode == 0 else ''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=2400)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    samples = []
    started = time.monotonic()
    for tick in range(args.seconds // 10):
        time.sleep(max(0, started + tick*10 - time.monotonic()))
        stats = command('docker', 'stats', '--no-stream', '--format', '{{json .}}',
                        'tochka-miniapp-1', 'tochka-postgres-1')
        metrics = command('docker', 'exec', 'tochka-miniapp-1', 'python', '-c',
                          "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8766/api/internal/metrics').read().decode())")
        try:
            rows = [json.loads(line) for line in stats.splitlines()]
            queue = json.loads(metrics)
            samples.append({'elapsed_seconds': tick*10,
                            'docker': {row['Name']: {'cpu_percent': row['CPUPerc'],
                                                     'memory': row['MemUsage']} for row in rows},
                            'queue': queue})
        except (ValueError, KeyError):
            samples.append({'elapsed_seconds': tick*10, 'sample_error': True})
    Path(args.output).write_text(json.dumps({'interval_seconds': 10, 'samples': samples}, indent=2) + '\n')
    print(json.dumps({'samples': len(samples), 'errors': sum('sample_error' in row for row in samples)}))


if __name__ == '__main__':
    main()
