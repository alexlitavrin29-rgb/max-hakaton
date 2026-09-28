"""Reproducible local subset of the official KLADR download; no streets/houses."""
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from dbfread import DBF

def main():
    root=Path('tmp/work-v3/kladr');out=Path('project/llm/data')
    rows=list(DBF(str(root/'KLADR.DBF'),encoding='cp866'))
    current={r['CODE']:r for r in rows if r['CODE'].endswith('00')}
    old={r['CODE']:r for r in rows}
    types={r['SCNAME']:r['SOCRNAME'] for r in DBF(str(root/'SOCRBASE.DBF'),encoding='cp866')}
    aliases={}
    for r in DBF(str(root/'ALTNAMES.DBF'),encoding='cp866'):
        before=old.get(r['OLDCODE']);after=current.get(r['NEWCODE'])
        if before and after and before['NAME']!=after['NAME']:
            aliases.setdefault(r['NEWCODE'],set()).add(before['NAME'])
    fields=['name','type','region','district','parent','source_id','code','kind','aliases']
    path=out/'settlements.csv.gz';counts=Counter();regions=set()
    with gzip.open(path,'wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for code,row in current.items():
            region=current.get(code[:2]+'0'*11)
            if not region: continue
            is_region=code[2:]=='0'*11
            # Districts are context, not settlements. Federal cities remain places.
            if not is_region and code[5:11]=='000000': continue
            district=current.get(code[:5]+'0'*8,{}) if code[2:5]!='000' else {}
            parent=current.get(code[:8]+'0'*5,{}) if code[8:11]!='000' and code[5:8]!='000' else {}
            region_name=region['NAME']+' '+types.get(region['SOCR'],region['SOCR'])
            kind='region' if is_region and row['SOCR']!='г' else 'city'
            w.writerow(dict(name=row['NAME'],type=types.get(row['SOCR'],row['SOCR']),region=region_name,
                district=district.get('NAME',''),parent=parent.get('NAME',''),source_id=code,
                code=code[:2]+'0'*11,kind=kind,aliases='|'.join(sorted(aliases.get(code,set())))))
            counts[row['SOCR']]+=1;regions.add(code[:2])
    meta=dict(source='ФНС России / КЛАДР',source_url='https://fias-file.nalog.ru/downloads/2026.07.07/base.7z',
              data_date='2026-07-07',downloaded='2026-09-23',records=sum(counts.values()),regions=sorted(regions),types=dict(counts),
              sha256=hashlib.sha256(path.read_bytes()).hexdigest(),archive_sha256=hashlib.sha256(Path('tmp/work-v3/base.7z').read_bytes()).hexdigest())
    (out/'settlements-manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(records=meta['records'],regions=len(regions),types=len(counts),bytes=path.stat().st_size)))

if __name__=='__main__': main()
