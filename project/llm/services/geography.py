"""Local, attributed city directory. Model-supplied region codes are never used."""

import csv
import gzip
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from .russian import grounded, signature
from rapidfuzz import process, fuzz


def norm(value):
    return re.sub(r"[^\w\s/]", " ", str(value).lower().replace("ё", "е")) .strip()


def words(value):
    return re.findall(r"[а-яa-z0-9]+", norm(value))


def same_words(value, evidence):
    """Allow simple case endings, but never an unrelated model-generated noun."""
    supplied = words(evidence)
    def stem(word):
        for ending in ("иями","ами","ями","ого","ему","ому","ий","ый","ой","ая","ое","ие","ые","ом","ем","ам","ах","ях","а","я","е","у","ы","и"):
            if word.endswith(ending) and len(word)-len(ending)>=3: return word[:-len(ending)]
        return word
    return grounded(value,evidence) or all(any(a == b or stem(a)==stem(b)
               for b in supplied) for a in words(value) if a not in {"в", "на", "по", "с"})


@lru_cache(maxsize=1)
def legacy_places():
    result = []
    regions = {}
    with (Path(__file__).parents[1] / "data" / "cities.csv").open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            code = row["kladr_id"][:2] + "0" * 11
            kind = row["region_type"]
            suffix = {"обл":"область", "Респ":"республика", "АО":"автономный округ", "Аобл":"автономная область"}.get(kind, kind)
            region = row["region"] if kind == "г" else row["region"] + " " + suffix
            regions[code] = dict(name=region, region=region, code=code, kind="region", zone=None)
            name = row["city"] or row["region"]
            offset = int(row["timezone"].replace("UTC", ""))
            result.append(dict(name=name, region=region, code=code, kind="city", zone=f"Etc/GMT{-offset:+d}"))
    # Federal cities are searched as cities; duplicate region choices would confuse selection.
    result.extend(r for code,r in regions.items() if code[:2] not in {"77", "78", "92"})
    return result


@lru_cache(maxsize=1)
def places():
    path=Path(__file__).parents[1]/'data'/'settlements.csv.gz'
    zones={(key(p['name']),p['code']):p['zone'] for p in legacy_places() if p['kind']=='city'}
    region_zones={}
    for (_,code),zone in zones.items():region_zones.setdefault(code,set()).add(zone)
    with gzip.open(path,'rt',encoding='utf-8') as f:
        result=list(csv.DictReader(f))
    for p in result:
        if p['kind']=='region':p['name']=p['region']
        p['zone']=zones.get((key(p['name']),p['code']))
        if not p['zone'] and len(region_zones.get(p['code'],[]))==1:p['zone']=next(iter(region_zones[p['code']]))
    return result


@lru_cache(maxsize=1)
def indexes():
    root=Path(__file__).parents[1]/'data';path=root/'settlements-index.json.gz'
    if path.exists():
        with gzip.open(path,'rt',encoding='utf-8') as f:data=json.load(f)
        if data['data_sha256']==hashlib.sha256((root/'settlements.csv.gz').read_bytes()).hexdigest():
            return ({k:set(v) for k,v in data['exact'].items()},{k:set(v) for k,v in data['morph'].items()})
    return build_indexes()


def build_indexes():
    exact={};morph={}
    for i,p in enumerate(places()):
        names=[p['name']]+p['aliases'].split('|')
        if p['kind']=='region':names.append(re.sub(r'\s+(?:область|Республика|республика|край|автономный округ)$','',p['name']))
        for name in names:
            if not name:continue
            exact.setdefault(key(name),set()).add(i)
            morph.setdefault(signature(name),set()).add(i)
    return exact,morph


ALIASES = {"спб":"санкт петербург", "петербург":"санкт петербург",
           "татарстан":"татарстан республика", "башкортостан":"башкортостан республика",
           "удмуртия":"удмуртская республика", "хмао":"ханты мансийский автономный округ югра автономный округ"}


def key(value):
    return " ".join(words(value))


@lru_cache(maxsize=1)
def legacy_indexes():
    exact={};lengths={}
    for p in legacy_places():
        exact.setdefault(key(p['name']),[]).append(p)
        lengths.setdefault(len(words(p['name'])),[]).append(p)
    return exact,lengths


def resolve_legacy(name,region=None):
    candidate=ALIASES.get(key(name),key(name))
    exact,lengths=legacy_indexes()
    matches=exact.get(candidate,[])
    if not matches:matches=[p for p in lengths.get(len(words(name)),[]) if same_words(p['name'],name)]
    if region:
        codes={p['code'] for p in resolve_legacy(region) if p['kind']=='region' or p['code'][:2] in {'77','78','92'}}
        matches=[p for p in matches if p['code'] in codes]
    return [dict(p) for p in matches]


def resolve(name, region=None):
    # Types and case endings are syntax; the identifier still comes only from KLADR.
    prefix=re.match(r'^(город\s+|гор\.\s*|г\.\s*|г\s+|село\s+|с\.\s*|деревня\s+|д\.\s*|пос[её]лок\s+|п\.\s*|пгт[. ]\s*|ст-ца\s+)',name.strip(),re.I)
    requested_type=None
    if prefix:
        word=key(prefix[0]);requested_type='город' if word in {'город','гор','г'} else 'село' if word in {'село','с'} else 'деревня' if word in {'деревня','д'} else 'станица' if word=='ст ца' else 'поселок'
        name=name.strip()[prefix.end():]
    candidate = ALIASES.get(key(name), key(name))
    exact,morph=indexes();ids=exact.get(candidate) or morph.get(signature(candidate)) or set()
    matched='exact'
    if not ids and len(candidate)>=4:
        closest=process.extract(candidate,exact.keys(),scorer=fuzz.ratio,score_cutoff=80,limit=5)
        ids=set().union(*(exact[n] for n,score,_ in closest if score>=max(80,closest[0][1]-2))) if closest else set()
        matched='suggestion'
    matches=[dict(places()[i],matched=matched) for i in sorted(ids)]
    if requested_type:matches=[p for p in matches if requested_type in key(p['type'])]
    if region:
        parts=[x.strip() for x in region.split(',')]
        regions=[p for p in places() if p['kind']=='region' or p['code'][:2] in {'77','78','92'} and p['name'] in {'Москва','Санкт-Петербург','Севастополь'}]
        codes={p['code'] for p in regions if any(same_words(part,p['region']) or same_words(p['region'],part) for part in parts)}
        matches=[p for p in matches if p['code'] in codes]
        district=next((x for x in parts if re.search(r'район|р-н',x,re.I)),None)
        if district:
            district=re.sub(r'район|р-н','',district,flags=re.I).strip()
            matches=[p for p in matches if same_words(district,p['district']) or same_words(district,p['parent'])]
    return matches


def label(place):
    return place["name"] if place["kind"] == "region" else f"{place.get('type','')} {place['name']} — {place['region']}"+(' — '+place['district']+' район' if place.get('district') else '')
