"""Russian amount grammar, independent of the amounts chosen by an LLM."""
import re
from decimal import Decimal, InvalidOperation
from .russian import analyzer

ONES='ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать'.split()
NUMBERS={w:i for i,w in enumerate(ONES)}
NUMBERS.update(dict(одна=1,две=2,двадцать=20,тридцать=30,сорок=40,пятьдесят=50,шестьдесят=60,
                   семьдесят=70,восемьдесят=80,девяносто=90,сто=100,двести=200,триста=300,
                   четыреста=400,пятьсот=500,шестьсот=600,семьсот=700,восемьсот=800,девятьсот=900))
NUMBERS.update(полтора=1.5,полторы=1.5,пара=2)
SCALES={'тысяча':1000,'тыс':1000,'к':1000,'кило':1000,'кусок':1000,'косарь':1000,'штука':1000,
        'миллион':1000000,'млн':1000000,'лям':1000000,'миллиард':1000000000,'млрд':1000000000}
PERIODS={'month':r'месяц|ежемесяч|месячн|\bмес\b','week':r'недел','day':r'\bдень\b|\bдня\b|сутк|ежеднев',
         'shift':r'смен','hour':r'час'}

def is_clarification(text):
    """Only an entire pay-period/bound/tax answer; compound or negated messages need extraction."""
    t=text.strip(' \t\r\n.,!').lower().replace('ё','е')
    return bool(re.fullmatch(r'(?:это |оплата |зарплата )?(?:(?:за |в )(?:месяц|неделю|день|сутки|смену|час)|'
                             r'ежемесячно|еженедельно|ежедневно|минимум|максимум|примерно|около|'
                             r'на руки|чистыми|(?:до|после) (?:вычета )?налогов)',t))

def boundary_kind(t):
    if re.search(r'не больше|не более|не выше|максимум|(?<!не )\bдо\s+(?!вычета|налог)',t):return 'max'
    if re.search(r'около|примерно|в районе|ориентир|порядка',t):return 'target'
    if re.search(r'\bот\b|не меньше|не менее|не ниже|хотя бы|минимум|минимальн\w*\s+зарплат',t):return 'min'
    return 'unspecified'


def source_context(evidence,text):
    """Preserve an adjacent operator if the model truncated the literal evidence."""
    start=text.casefold().find(evidence.casefold())
    prefix=re.search(r'(?<!\w)(не\s+(?:менее|меньше|больше|более|ниже|выше)|от|до|минус|[-−]|около|примерно|хотя бы)\s*$',text[:start],re.I) if start>=0 else None
    return (prefix[0] if prefix else '')+evidence

def amounts(text):
    text=text.lower().replace('ё','е').replace('\u00a0',' ')
    def decimal_words(match):
        whole=amounts(match[1]);fraction=amounts(match[2])
        divisor=10 if match[3].startswith('десят') else 100 if match[3].startswith('сот') else 1000
        if len(whole)!=1 or len(fraction)!=1 or not 0<=fraction[0]['value']<divisor:raise ValueError('amount')
        return str(whole[0]['value']+fraction[0]['value']/divisor)
    text=re.sub(r'([а-я\d ]+)\s+цел(?:ых|ая|ое)\s+([а-я\d ]+?)\s+(десятых|десятая|сотых|сотая|тысячных|тысячная)',decimal_words,text)
    # A dash between amounts is a range separator, not a negative second amount.
    text=re.sub(r'(?<=\d)\s*[-–—]\s*(?=\d)',' до ',text)
    chunks=[];total=Decimal(0);part=Decimal(0);active=False;scaled=False;start=0;last=0
    def flush():
        nonlocal total,part,active,scaled
        if active: chunks.append(dict(value=float(total+part),scaled=scaled,start=start,end=last))
        total=part=Decimal(0);active=False;scaled=False
    for m in re.finditer(r'-?\d+(?:[ \u202f]\d{3})*(?:[.,]\d+)*|[а-я]+',text):
        word=m[0];number=None;scale=None
        if re.match(r'-?\d',word):
            clean=word.replace(' ','').replace('\u202f','')
            if re.fullmatch(r'-?\d{1,3}(?:\.\d{3})+',clean): clean=clean.replace('.','')
            elif re.fullmatch(r'-?\d{1,3},\d{3}',clean): raise ValueError('separator')
            else: clean=clean.replace(',','.')
            try: number=Decimal(clean)
            except InvalidOperation: raise ValueError('amount') from None
        else:
            lemmas=[word]+[p.normal_form for p in analyzer().parse(word)[:4]]
            number=next((Decimal(NUMBERS[x]) for x in lemmas if x in NUMBERS),None)
            scale=next((SCALES[x] for x in lemmas if x in SCALES),None)
        if number is not None:
            if not active: start=m.start()
            part+=number;active=True;last=m.end()
        elif scale:
            # The standalone preposition «к» is not an implicit thousand rubles.
            if word=='к' and not active:continue
            if not active: start=m.start();active=True;part=Decimal(1)
            total+=part*scale;part=Decimal(0);scaled=scale;last=m.end()
        else: flush()
    flush()
    return chunks

def parse(text,previous=None):
    """Return value + a specific unresolved issue; never infer a pay-period conversion."""
    t=text.lower().replace('ё','е')
    # A unit-only reply completes the pending number, not an implicit "one thousand".
    unit=re.fullmatch(r'(тысяч(?:и)?|тыс\.?|рубл(?:ей|и|ях)|руб\.?)([\s,]*(?:(?:за|в)\s+(?:месяц|неделю|день|смену|час))?[\s,]*(?:минимум|максимум|примерно)?)',t.strip())
    if unit and previous and (previous.get('lower') is not None or previous.get('upper') is not None):
        amount=previous.get('lower') if previous.get('lower') is not None else previous['upper']
        t=str(amount)+' '+t
    if re.search(r'доллар|евро|юан|тенге|гривн|\busd\b|\beur\b|[$€]',t):return None,'currency'
    t=re.sub(r'\bминус\s+(?=\d)','-',t)
    # Correction and historical income are context, not additional desired bounds.
    t=re.split(r'\bтеперь\b|\bа\s+(?!также)',t)[-1] if re.search(r'раньше|прежде|\bне\s+\d',t) else t
    period=next((k for k,p in PERIODS.items() if re.search(p,t)),None)
    tax='net' if re.search(r'на руки|после (?:вычета )?налог|чистыми',t) else 'gross' if re.search(r'до (?:вычета )?налог|гросс|gross',t) else 'unknown'
    try: nums=amounts(t)
    except ValueError as e: return None,str(e)
    kind=boundary_kind(t)
    if not nums and previous and (period or kind!='unspecified' or tax!='unknown'):
        merged={**previous,'period':period or previous.get('period'),'tax':tax if tax!='unknown' else previous.get('tax','unknown')}
        if kind!='unspecified':
            amount=previous.get('lower') or previous.get('upper')
            merged.update(kind=kind,lower=None if kind=='max' else amount,upper=amount if kind=='max' else None)
        values=[v for v in (merged.get('lower'),merged.get('upper')) if v is not None]
        if not values or any(v<=0 for v in values):return merged,'invalid'
        if not merged.get('period'):return merged,'period'
        if merged['kind']=='unspecified':return merged,'kind'
        floor,ceiling={'month':(1000,2000000),'week':(200,500000),'day':(50,100000),'shift':(50,100000),'hour':(10,100000)}[merged['period']]
        return merged,'confirm' if any(v<floor or v>ceiling for v in values) else None
    if not nums: return None,'amount'
    if len(nums)>2: return None,'bounds'
    # Shared thousand unit in a range: 70–90 тысяч.
    if len(nums)==2 and nums[1]['scaled'] and not nums[0]['scaled'] and nums[0]['value']<1000:
        nums[0]['value']*=nums[1]['scaled']
    values=[int(n['value']) if n['value'].is_integer() else n['value'] for n in nums]
    if re.search(r'\bминус\b',t):values[0]=-abs(values[0])
    if kind=='unspecified' and previous and previous.get('kind') in {'min','max','target'}:kind=previous['kind']
    if len(values)==2:
        if not re.search(r'\bдо\b|\bмежду\b|[-–—]',t): return None,'bounds'
        kind='range';lower,upper=values
    elif kind=='max':lower=None;upper=values[0]
    else:lower=values[0];upper=None
    if previous:
        period=period or previous.get('period')
        if tax=='unknown':tax=previous.get('tax','unknown')
    value=dict(kind=kind,lower=lower,upper=upper,period=period,tax=tax,currency='RUB')
    if any(v<=0 for v in values) or (upper is not None and lower is not None and lower>upper): return value,'invalid'
    if len(values)==1 and values[0]<1000 and not re.search(r'руб|₽|тыс|\d\s*к\b',t) and not period: return value,'scale'
    if not period: return value,'period'
    if kind=='unspecified':return value,'kind'
    floor,ceiling={'month':(1000,2000000),'week':(200,500000),'day':(50,100000),'shift':(50,100000),'hour':(10,100000)}[period]
    if any(v<floor or v>ceiling for v in values): return value,'confirm'
    return value,None

QUESTIONS={'amount':'На какую оплату будем ориентироваться? Нужны сумма и период — например, за месяц или за смену.',
 'currency':'Поиск настроен на зарплату в рублях. Какую сумму в рублях будем искать? Пересчитывать её без вашего выбора не буду.',
 'separator':'Помогите, пожалуйста, уточнить сумму: сколько это рублей? Можно написать число без точек и запятых.',
 'bounds':'Уточним, пожалуйста: какую сумму или диапазон оплаты будем искать?',
 'invalid':'Кажется, в сумме могла быть опечатка. Нужна сумма больше нуля, а в диапазоне — сначала меньшая, потом большая.',
 'scale':'Это сумма в рублях или в тысячах рублей? И за какой период?',
 'period':'Подскажите, пожалуйста, это оплата за месяц, неделю, день, смену или час?',
 'kind':'Эта сумма — минимум, максимум или примерный ориентир?',
 'confirm':'Хочу убедиться, что сумма записана верно. Всё правильно или её нужно поправить?'}

def display(value):
    def n(x):return f'{x:,}'.replace(',',' ')
    kind=value['kind'];a=value.get('lower');b=value.get('upper')
    text=f'от {n(a)} до {n(b)}' if kind=='range' else f'до {n(b)}' if kind=='max' else f'около {n(a)}' if kind=='target' else f'от {n(a)}' if kind=='min' else n(a)
    period={'month':'месяц','week':'неделю','day':'день','shift':'смену','hour':'час'}.get(value.get('period'),'неуточнённый период')
    return text+' ₽ за '+period+{'net':' на руки','gross':' до вычета налогов','unknown':''}[value.get('tax','unknown')]

def assess(job,value):
    if not isinstance(value,dict): return None,None
    missing=[]
    period=getattr(job,'salary_period',None)
    tax=getattr(job,'salary_tax',None)
    if period!=value.get('period'):missing.append('период оплаты вакансии не подтверждён для выбранного периода')
    if value.get('tax') not in {None,'unknown',tax}:missing.append('налоговое основание зарплаты в карточке не подтверждено')
    if missing:return None,'; '.join(missing)
    lo,hi=job.salary_from,job.salary_to;lower,upper=value.get('lower'),value.get('upper')
    if value['kind']=='target': return None,'близость оплаты к ориентиру требует уточнения'
    if lower is not None and hi is not None and hi<lower: return 'Зарплата ниже выбранной',None
    if upper is not None and lo is not None and lo>upper: return 'Зарплата выше выбранной верхней границы',None
    if lower is not None and (lo is None or lo<lower): return None,'нижняя граница зарплаты не подтверждена'
    if upper is not None and (hi is None or hi>upper): return None,'верхняя граница зарплаты не подтверждена'
    return None,None
