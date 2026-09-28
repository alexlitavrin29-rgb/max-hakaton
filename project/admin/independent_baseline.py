"""Read-only snapshot of effective deployment, public scenario and runtime inputs.

Run from repository root. Docker reads may require host permission. Never prints secrets.
"""
import argparse
import hashlib
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def files(root):
    selected = set()
    for name in ('project/llm', 'project/admin'):
        selected.update(p for p in (root / name).rglob('*.py') if 'tests' not in p.parts)
        selected.update((root / name).glob('*requirements*.txt'))
    for name in ('project/llm/data', 'project/llm/prompts', 'project/data/knowledge'):
        selected.update(p for p in (root / name).rglob('*') if p.is_file())
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(selected)}


REMOTE = '''import json,os,sys,platform,importlib.metadata
from pathlib import Path
from project.admin.independent_baseline import files,digest
from project.llm.config import LLMSettings
settings=LLMSettings.from_env()
hashes=files(Path('/app'))
packages={}
for name in ['openai','httpx','fastapi','asyncpg','pydantic']:
 try: packages[name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError: packages[name]=None
print(json.dumps(dict(python=platform.python_version(),files=hashes,input_sha256=digest(hashes),
 settings=dict(provider=os.getenv('LLM_PROVIDER','polza'),base_url=settings.base_url,
 model=settings.model,timeout_seconds=settings.timeout_seconds),
 packages=packages,
 environment_source='docker exec inherited container environment; settings resolved with LLMSettings.from_env'),ensure_ascii=False))
'''


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen('http://127.0.0.1:18765/api/state', timeout=15) as response:
        state = json.load(response)
    (out / 'scenario.json').write_text(json.dumps(state['config'], ensure_ascii=False, indent=2), encoding='utf-8')
    local = files(Path.cwd())
    result = dict(captured_utc=datetime.now(timezone.utc).isoformat(), revision=state['revision'],
                  published_id=state['published_id'], api_reported_model=state['model'],
                  scenario_sha256=digest(state['config']), local_files=local,
                  local_input_sha256=digest(local), containers={})
    # Send definitions via stdin rather than copy a helper into either deployment.
    definitions = 'import hashlib,json\n' + __import__('inspect').getsource(digest) + '\n' + __import__('inspect').getsource(files)
    remote = REMOTE.replace('from project.admin.independent_baseline import files,digest', definitions)
    for container in ['support-router-admin-1', 'support-router-llm-1']:
        meta = subprocess.run(['docker','inspect','--format',
            '{{json .Id}} {{json .Image}} {{json .Created}} {{json .State.StartedAt}}', container],
            check=True, capture_output=True, text=True).stdout.strip()
        proc = subprocess.run(['docker','exec','-i',container,'python','-'], input=remote,
                              capture_output=True,text=True,encoding='utf-8')
        record = dict(identity=meta, exit_code=proc.returncode)
        if proc.returncode == 0:
            record.update(json.loads(proc.stdout))
        else:
            record['error'] = proc.stderr[-1500:]
        result['containers'][container] = record
    (out / 'baseline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['local_files','containers']},ensure_ascii=False))
    for name, data in result['containers'].items():
        print(name, json.dumps({k:v for k,v in data.items() if k!='files'},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
