"""Precompute the same morphology index to avoid a slow first dialogue."""
import gzip,hashlib,json
from pathlib import Path
from project.llm.services.geography import build_indexes

if __name__=='__main__':
    root=Path('project/llm/data');exact,morph=build_indexes()
    payload=dict(data_sha256=hashlib.sha256((root/'settlements.csv.gz').read_bytes()).hexdigest(),
                 exact={k:sorted(v) for k,v in exact.items()},morph={k:sorted(v) for k,v in morph.items()})
    with gzip.open(root/'settlements-index.json.gz','wt',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False)
    print(json.dumps(dict(exact=len(exact),morph=len(morph))))
