"""Execute this submission's DATA-API checks; never print credentials or bodies.

Public checks are read-only. Full mode requires a dedicated test session in
DATA_API_SESSION. Cleanup runs even on assertion failure. This deliberately
supports only the JSONPath subset used by DATA-API.yaml, not arbitrary suites.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jsonschema
import yaml


def extract(value, path):
    if not re.fullmatch(r'\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*', path):
        raise ValueError('Unsupported JSONPath')
    for key, index in re.findall(r'\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]', path):
        value = value[key] if key else value[int(index)]
    return value


def substitute(value, variables):
    if isinstance(value, str):
        return re.sub(r'\$\{([^}]+)\}', lambda m: str(variables[m[1]]), value)
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    return value


def run(config, base, token='', full=False):
    variables, results = {}, []

    def call(step):
        request = substitute(step.get('request', {}), variables)
        path = step['path']
        for key, value in request.get('path', {}).items():
            from urllib.parse import quote
            path = path.replace('{' + key + '}', quote(str(value), safe=''))
        if request.get('query'):
            path += '?' + urlencode(request['query'])
        headers = {**config['api'].get('defaultHeaders', {}), **request.get('headers', {})}
        if step['role'] != 'public':
            headers['Authorization'] = 'Bearer ' + token
        body = json.dumps(request['body']).encode() if 'body' in request else None
        started = time.monotonic()
        req = Request(base + path, data=body, headers=headers, method=step['method'])
        try:
            response = urlopen(req, timeout=step.get('timeoutMs', 5000) / 1000)
        except HTTPError as error:
            response = error
        with response:
            status, content_type = response.status, response.headers.get('Content-Type', '')
            data = json.load(response)
        expected = step.get('expected', {})
        assert status in expected.get('statusCodes', [200]), f'Unexpected HTTP {status}'
        if 'contentType' in expected:
            assert content_type.split(';')[0] == expected['contentType'], 'Content-Type mismatch'
        for field in expected.get('requiredFields', []):
            assert field in data, f'Missing field {field}'
        if 'bodySchema' in expected:
            jsonschema.validate(data, expected['bodySchema'])
        for name, path in step.get('extract', {}).items():
            variables[name] = extract(data, path)
        results.append(dict(id=step['id'], status=status, ok=True,
                            elapsed_ms=round((time.monotonic() - started) * 1000)))

    error = None
    try:
        for step in config['checks']:
            if step['role'] != 'public' and not full:
                results.append(dict(id=step['id'], skipped='requires dedicated MAX test session'))
                continue
            time.sleep(.55)  # Respect the service's action rate limit.
            call(step)
    except Exception as caught:
        error = type(caught).__name__
        results.append(dict(id=step['id'], ok=False, error=error))
    finally:
        if full:
            for step in config.get('cleanup', []):
                try:
                    call(step)
                except KeyError:
                    results.append(dict(id=step['id'], skipped='object was not created'))
                except Exception as caught:
                    error = type(caught).__name__
                    results.append(dict(id=step['id'], ok=False, error=error))
    return dict(base=base, mode='full' if full else 'public', ok=error is None, results=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', help='Override only for local QA; DATA-API retains the public HTTPS URL')
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--output', default='api-check-results.json')
    args = parser.parse_args()
    config = yaml.safe_load(Path('DATA-API.yaml').read_text(encoding='utf-8'))
    token = os.getenv('DATA_API_SESSION', '')
    if args.full and not token:
        parser.error('Full mode needs DATA_API_SESSION for a dedicated test account')
    result = run(config, (args.base_url or config['api']['baseUrl']).rstrip('/'), token, args.full)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['ok'] else 1)
