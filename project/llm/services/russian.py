"""General morphology shared by validation and dictionary lookup."""
import re
from functools import lru_cache
from pymorphy3 import MorphAnalyzer

@lru_cache(maxsize=1)
def analyzer(): return MorphAnalyzer()

def tokens(text): return re.findall(r'[а-яёa-z0-9]+',str(text).lower().replace('ё','е'))

@lru_cache(maxsize=20000)
def forms(word):
    return frozenset([word]+[p.normal_form.replace('ё','е') for p in analyzer().parse(word)[:4]])

def grounded(value,evidence):
    source=[forms(w) for w in tokens(evidence)]
    return all(any(forms(w)&s for s in source) for w in tokens(value) if w not in {'в','на','по','с','для','и'})

def signature(text):
    return ' '.join(analyzer().parse(w)[0].normal_form.replace('ё','е') for w in tokens(text))
