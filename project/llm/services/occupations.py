"""Vocabulary is supporting evidence, never a closed list of allowed professions."""
import csv,gzip,re,json
from pathlib import Path
from functools import lru_cache
from rapidfuzz import process,fuzz
from .russian import grounded,signature,tokens,analyzer

# Abbreviations expand tokens, not whole hardcoded user messages or test amounts.
ABBREVIATIONS={'чпу':'числовым программным управлением','кипиа':'контрольно измерительных приборов и автоматики',
 'пто':'производственно технического отдела','жкх':'жилищно коммунального хозяйства','смм':'smm','айти':'it'}

def expand(text):
    for short,long in ABBREVIATIONS.items():text=re.sub(r'\b'+short+r'\b',long,text,flags=re.I)
    return text

@lru_cache(maxsize=1)
def vocabulary():
    with gzip.open(Path(__file__).parents[1]/'data'/'occupations.csv.gz','rt',encoding='utf-8') as f:
        rows=list(csv.DictReader(f))
    path=Path(__file__).parents[1]/'data'/'vacancy-title-sample.json'
    if path.exists():rows.extend(dict(code='trudvsem:'+r['id'],name=r['title']) for r in json.loads(path.read_text(encoding='utf-8'))['items'])
    return rows

@lru_cache(maxsize=256)
def suggestions(text):
    titles=[r['name'] for r in vocabulary()]
    return [dict(name=n,code=vocabulary()[i]['code']) for n,score,i in process.extract(text,titles,scorer=fuzz.WRatio,limit=3,score_cutoff=75)]

@lru_cache(maxsize=1)
def dictionary_index():return {signature(row['name']):row for row in vocabulary()}

def lookup(text):return dictionary_index().get(signature(text))

def normalize(value,evidence,context=''):
    if re.search(r'\bне\b|\bраньше\b|\bпрежде\b',evidence,re.I):
        selected=re.split(r'\bа\s+(?!также)|\bтеперь\b',evidence,flags=re.I)[-1]
        if selected.strip() and grounded(expand(value),expand(selected)):evidence=selected
    if grounded(expand(value),expand(evidence)):
        # Remove request syntax, then verify that qualifications were not dropped.
        syntax={'работа','работать','искать','хотеть','желать','желаемый','профессия','вакансия',
                'рассматривать','нужный','нужен','требоваться','быть','выбрать','выбирать','интересовать','должность','только'}
        heading,separator,tail=evidence.partition(':')
        if separator and set(signature(heading).split()) <= syntax|{'новый'}:
            evidence=tail
        meaningful=[]
        for word in tokens(expand(evidence)):
            parse=analyzer().parse(word)[0]
            if parse.normal_form in syntax or parse.tag.POS in {'PREP','CONJ','NPRO'}:continue
            if grounded(word,context) and not grounded(word,expand(value)):continue
            meaningful.append(word)
        if not grounded(' '.join(meaningful),expand(value)):return None
        canonical=lookup(value)
        return canonical['name'][:1].lower()+canonical['name'][1:] if canonical else value.strip()
    # Vocabulary proximity is a suggestion, not permission to replace the person's words.
    return None
