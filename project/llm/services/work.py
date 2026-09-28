"""Validated work conditions and vacancy matching for the draft work scenario."""

import re
import secrets
import time

from .geography import key, label, resolve_legacy as resolve, resolve as resolve_full, same_words
from . import money
from .llm import LLMError
from ..config import LLMConfigurationError
from ..channels.max import callback, link, message
from ..integrations.trudvsem import TrudvsemError


SEARCH_ALTERNATIVES = (
    'Можно продолжить поиск на других сайтах: '
    '[HeadHunter](https://hh.ru/search/vacancy) и '
    '[SuperJob](https://www.superjob.ru/vacancy/search/). '
    'Там нужно заново выбрать город, профессию и остальные условия — '
    'из бота они автоматически не переносятся.'
)


FIELDS = {"query":"Профессия", "city":"Место поиска", "age":"Возраст соискателя",
          "education_level":"Образование", "experience":"Опыт", "employment":"Занятость",
          "schedule":"График", "salary":"Зарплата", "housing":"Проживание"}
ENUMS = {
    "education_level":{"no_professional":"без профессионального образования", "general":"школа",
                       "vocational":"колледж / техникум", "higher":"высшее", "unspecified":"уровень не уточнён"},
    "employment":{"part_time":"подработка", "full_time":"полная занятость", "temporary":"временная работа"},
}
CONTRACT = '''
Для ветки work верни work_fields вместо values. Формат каждого поля:
{"status":"known|unknown|any|clarify|declined","value":значение или null,
 "evidence":"ДОСЛОВНЫЙ фрагмент НОВОГО сообщения","subject":"applicant|parent"}.
Не возвращай пропущенные поля и известные данные из контекста. Не додумывай значения.
Поля: query (только названная профессия, нормальная форма), city (город/регион в
именительном падеже, при названном регионе формат "город, регион"), age (целые годы
СОИСКАТЕЛЯ), education_level (no_professional/general/vocational/higher/unspecified),
experience (целые годы опыта, без опыта=0), employment (part_time/full_time/temporary),
schedule (точное пожелание к графику), salary (минимальная сумма в рублях), housing (true).
Полный рабочий день — schedule, полная занятость — employment. Не подменяй одно другим.
Любая работа/профессия не важна: query.status=any. Жильё не нужно: housing.status=any.
"Не знаю"=unknown, "неважно/снять условие"=any, противоречие=clarify,
отказ назвать возраст=declined. any для city только при ЯВНОМ выборе любого места/всей России.
Если спрашивали возраст, "20" относится к возрасту. Подработка не определяет график.
После колледжа означает vocational; учусь в колледже НЕ означает оконченное образование.
Не превращай пожелания, зарплату или слова "не знаю" в профессию. query не придумывать.
Возраст, опыт, образование и работа родителя НЕ относятся к ребёнку. Помечай subject=parent
для фактов о взрослом, subject=applicant для фактов о соискателе. values может содержать
только явно названную role. НИКАКИХ region_code: регион проверяет код по справочнику.
Если человек хочет напоминание или намерен посмотреть вакансии позже, work_action=remind.
Если просит показать/искать с текущими условиями, work_action=search.
Упоминание образования внутри поиска работы не переключает ветку на education.
'''


def field(status="unknown", value=None, evidence=""):
    return dict(status=status, value=value, evidence=evidence, subject="applicant")


def deterministic_fields(text):
    """Return only explicitly cued work fields; directories and money parser remain authoritative."""
    result={}
    age=re.search(r'(?<!\w)(?:мне|мой\s+возраст|возраст(?:\s+соискателя)?|соискателю)\s*[:—-]?\s*(\d{1,3})(?:\s*(?:лет|года|год))?\b',text,re.I)
    if not age:
        age=re.search(r'(?<!опыт\s)(?<!стаж\s)(?<!\w)(\d{1,3})\s*(?:лет|года|год)\b',text,re.I)
    if age and 1<=int(age[1])<=100:
        result['age']={**field('known',int(age[1]),age[0]),'intent':'desired'}
    salary=re.search(r'(?:(?:минимальн\w*\s+)?(?:зарплат\w*|оплат\w*)\s*[:—-]?\s*[^,;!?]+|'
                     r'(?<!\w)(?:от|до|около|примерно|минимум|максимум|не\s+ниже)\s+\d[\d\s.,]*\s*(?:тыс\w*|к\b|руб\w*|₽)[^,;.!?]*)',text,re.I)
    if salary:
        result['salary']={**field('known',None,salary[0].strip()),'intent':'desired'}
    tokens=list(re.finditer(r'[а-яё]+',text,re.I))
    discovered=[]
    for size in range(min(4,len(tokens)),0,-1):
        for start in range(len(tokens)-size+1):
            raw=text[tokens[start].start():tokens[start+size-1].end()]
            if key(raw) in {'город','область','республика','край','округ','автономный округ','работа','возраст','место'}:
                continue
            before=text[:tokens[start].start()]
            after=text[tokens[start+size-1].end():]
            left,right=before.rstrip(),after.lstrip()
            place_context=(
                (not left and (not right or right[0] in ',;.!?—-')) or
                bool(re.search(r'(?:\bв|\bво|\bгород|\bместо)\s*[:—-]?$',left,re.I)) or
                bool(left and left[-1] in ',;.!?—-' and (not right or right[0] in ',;.!?—-'))
            )
            if not place_context:
                continue
            if re.search(r'(?:республика|область|край|округ)\s*$',before,re.I) or re.match(r'\s+(?:республика|область|край|округ)\b',after,re.I):
                continue
            options=[p for p in resolve_full(raw) if p.get('matched')=='exact' and p.get('kind')=='city'
                     and len(key(p['name']).split())==len(key(raw).split()) and same_words(p['name'],raw) and same_words(raw,p['name'])]
            if not options:
                options=[dict(p,matched='exact') for p in resolve(raw) if p.get('kind')=='city'
                         and len(key(p['name']).split())==len(key(raw).split()) and same_words(p['name'],raw) and same_words(raw,p['name'])]
            if options:discovered.append((size,raw,options))
        if discovered:break
    if discovered:
        names={key(p['name']) for _,_,options in discovered for p in options}
        if len(names)==1:
            _,raw,options=discovered[0]
            named_region=(next((p['region'] for p in options if same_words(p['region'],text)),None)
                          if re.search(r'область|республика|край|автономный\s+округ|округ',text,re.I) else None)
            value=options[0]['name']+(', '+named_region if named_region else '') if len(options)==1 or named_region else raw
            result['city']={**field('known',value,text.strip() if named_region else raw),'intent':'desired'}
    region=r'(?:[а-яё-]+(?:\s+[а-яё-]+){0,3}\s+)?(?:область|республика|край|автономный\s+округ|округ)'
    place_match=re.search(rf'(?<!\w)(?:город|гор\.|г\.)\s*([а-яё-]+(?:\s+[а-яё-]+){{0,3}}?)(?:\s*,\s*({region}))?(?=\s*(?:,|;|\.|!|\?|—|мне\b|зарплат\w*|оплат\w*|$))',text,re.I)
    if not place_match and 'city' not in result:
        place_match=re.search(rf'(?<!\w)(?:работ\w*\s+|ищ\w*\s+)?(?:в|во)\s+([а-яё-]+(?:\s+[а-яё-]+){{0,3}}?)(?:\s*,\s*({region}))?(?=\s*(?:,|;|\.|!|\?|—|мне\b|зарплат\w*|оплат\w*|$))',text,re.I)
    if not place_match:
        leading=re.match(rf'\s*([а-яё-]+(?:\s+[а-яё-]+){{0,2}})(?:\s*,\s*({region}))?\s*(?=,|;|\.|!|\?|—)',text,re.I)
        if leading:
            name=leading.group(1).strip();named_region=leading.group(2).strip() if leading.group(2) else None
            options=resolve_full(name,named_region)
            if any(p.get('matched')=='exact' for p in options):place_match=leading
    if place_match and 'city' not in result:
        name=place_match.group(1).strip();named_region=place_match.group(2).strip() if place_match.group(2) else ''
        options=resolve_full(name,named_region or None)
        exact=[p for p in options if p.get('matched')=='exact']
        explicit=bool(re.search(r'(?<!\w)(?:город|гор\.|г\.|работ\w*\s+(?:в|во)|ищ\w*\s+(?:в|во))\s*',place_match[0],re.I))
        if exact or explicit:
            canonical=exact[0]['name'] if len(exact)==1 else name
            value=canonical+(', '+named_region if named_region else '')
            result['city']={**field('known',value,place_match[0].strip()),'intent':'desired'}
    if 'city' in result and 'query' not in result:
        pair=re.fullmatch(r'\s*([а-яё-]+(?:\s+[а-яё-]+){0,5})\s*,\s*([а-яё-]+(?:\s+[а-яё-]+){0,3})\s*[.!?]?\s*',text,re.I)
        if pair and same_words(pair.group(2),result['city']['value']):
            profession=pair.group(1).strip()
            if key(profession) not in {'работа','вакансия','профессия','любая работа'}:
                from .occupations import normalize
                normalized=normalize(profession,profession)
                if normalized:
                    result['query']={**field('known',normalized,profession),'intent':'desired'}
    return result


def deterministic_complete(text, fields):
    """Conservatively prove that no profession or unsupported condition remains."""
    if not fields:
        return False
    salary=fields.get('salary')
    if salary:
        parsed,issue=money.parse(salary['evidence'])
        compound_default=(issue=='period' and {'city','age','salary'} <= set(fields)
                          and re.search(r'зарплат\w*',salary['evidence'],re.I))
        if (issue and not compound_default) or not parsed or (parsed.get('period') not in {None,'month'}) or parsed.get('kind') not in {'min','range'}:
            return False
        if compound_default:
            salary['default_period']='month'
    covered=key(text).split()
    for item in fields.values():
        evidence=item.get('evidence') or ''
        if evidence and key(evidence)==key(text):
            # City discovery may use the whole message as grounding; remove only
            # the directory-confirmed city and region instead.
            value=item.get('value') or ''
            remove=set(key(value).split())
            covered=[word for word in covered if word not in remove]
        else:
            remove=set(key(evidence).split())
            covered=[word for word in covered if word not in remove]
    allowed={'работа','работу','ищу','найти','покажи','показать','в','во','город','место','мне','мой','возраст',
             'соискателя','соискателю','лет','год','года','зарплата','зарплату','минимальная','минимум','от','не','ниже',
             'тыс','тысяч','тысячи','руб','рублей','рубля','месяц','мес','ежемесячно','хочу'}
    return all(token.isdigit() or token in allowed for token in covered)


def explicit_numbers(evidence):
    numeric=[]
    for n in re.findall(r"\d+(?:[ .,]\d+)*",evidence):
        try: numeric.append(float(n.replace(" ", "").replace(",", ".")))
        except ValueError: continue
    ones="ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split()
    numbers={word:i for i,word in enumerate(ones)}
    numbers.update(dict(двадцать=20,тридцать=30,сорок=40,пятьдесят=50,шестьдесят=60,семьдесят=70,восемьдесят=80,девяносто=90,сто=100,одна=1,две=2))
    value=0
    for word in key(evidence).split():
        if word in numbers: value+=numbers[word]
        elif value: numeric.append(value);value=0
    if value: numeric.append(value)
    return numeric


def evidence_context(evidence,text):
    """Keep adjacent negation; a literal span can still omit its operator."""
    start=text.casefold().find(evidence.casefold())
    if start<0:return evidence
    prefix=re.search(r'\b(?:не|нет|без)\s*$',text[:start],re.I)
    suffix=re.match(r'\s+(?:нет\b|не\s+нуж\w*)',text[start+len(evidence):],re.I)
    return (prefix[0] if prefix else '')+evidence+(suffix[0] if suffix else '')


def validate_fields(raw, text, role):
    """Ground every update in the current message; reject unrelated values and codes."""
    result = {}
    if not isinstance(raw, dict): return result
    for name, item in raw.items():
        if name not in FIELDS or not isinstance(item, dict): continue
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip() or evidence.casefold() not in text.casefold(): continue
        if item.get("subject")=="parent" or (name in {"age","education_level","experience"} and item.get("subject")!="applicant"): continue
        status, value = item.get("status"), item.get("value")
        phrase = key(evidence_context(evidence,text))
        if not isinstance(status,str) or status not in {"known", "unknown", "any", "clarify", "declined"}: continue
        if status == "declined" and (name != "age" or not re.search(r"не (?:хочу|буду|скажу)|отказ|скрою", phrase)): continue
        if status == "unknown" and not re.search(r"не знаю|неизвест|не определ|затрудня", phrase): continue
        if status == "any" and not re.search(r"люб|вс[еяю]|разн\w*|не ?важ|не нуж|не\s+(?:огранич\w*|выбира\w*)|без огранич|сня|убра|не требуется|не имеет значения", phrase): continue
        if status == "any" and name=="city" and not re.search(r"росси|люб.*(?:город|регион|мест)|(?:город|регион|мест).*не ?важ",phrase): continue
        if status != "known":
            result[name] = field(status, None, evidence)
            continue
        if name in {'query','schedule','housing'} and re.search(r'\bне\b',phrase) and not re.search(r'\bа\b|\bтеперь\b',phrase):
            result[name]=field('clarify',None,evidence);continue
        if name=='salary' and isinstance(value,dict):
            parsed,issue=money.parse(evidence)
            result[name]={**field('clarify' if issue else 'known',parsed,evidence),'issue':issue}
            continue
        if name in {"age", "experience", "salary"}:
            if type(value) is not int or not 0 <= value <= {"age":100,"experience":70,"salary":10_000_000}[name]:
                result[name] = field("clarify", None, evidence); continue
            if name=="age" and value==0:
                result[name]=field("clarify",None,evidence); continue
            numeric = explicit_numbers(evidence)
            if name == "salary" and re.search(r"тыс|\d\s*к\b", phrase): numeric += [n*1000 for n in numeric]
            if name=="salary" and re.search(r"\bдо\s+\d",phrase) and not re.search(r"\bот\s+\d",phrase):
                result[name]=field("clarify",None,evidence); continue
            if name == "experience" and re.search(r"без опыта|нет опыта|опыта нет", phrase): numeric.append(0)
            if value not in numeric:
                result[name] = field("clarify", None, evidence); continue
            if name == "age" and re.search(r"\bмне\b", phrase) and (role == "parent" or re.search(r"сын|доч|реб[её]н", text, re.I)):
                children=re.findall(r"(?:сыну|сына|дочери|дочке|ребенку)\s+(\d{1,3})",phrase)
                if str(value) not in children: continue
        elif name in ENUMS:
            if name=="education_level" and re.search(r"(?:не|нет|без)\s+высш|высш\w*(?:\s+образован\w*)?\s+(?:нет|не\b)",phrase):
                result[name] = field("clarify", None, evidence); continue
            if name=="education_level" and re.search(r"без.*образован|нет.*образован",phrase): value="no_professional"
            if not isinstance(value,str) or value not in ENUMS[name]: result[name] = field("clarify", None, evidence); continue
            clues = {"no_professional":r"без.*образован|нет.*образован|без.*диплом", "general":r"школ|класс",
                     "vocational":r"колледж|техникум|средн.*профессион", "higher":r"вуз|университет|высш",
                     "unspecified":r"образован|диплом", "part_time":r"подработ|частич|неполн",
                     "full_time":r"полн", "temporary":r"временн"}
            if not re.search(clues[value], phrase): continue
            if name == "education_level" and value in {"vocational", "higher"} and re.search(r"учусь|учится|студент", phrase) and not re.search(r"окончил|закончил|диплом|после", phrase):
                result[name] = field("clarify", None, evidence); continue
        elif name == "housing":
            if value is not True or not re.search(r"жиль|жилищ|прожив|общежит", phrase): continue
        else:
            if not isinstance(value, str) or not value.strip() or len(value)>150: continue
            if name=="schedule": value=evidence.strip()
            if not same_words(value, evidence): result[name] = field("clarify", None, evidence); continue
            if name == "query" and re.search(r"не ?важ|любая работа|не знаю", phrase):
                result[name] = field("any" if "знаю" not in phrase else "unknown", None, evidence); continue
        result[name] = field("known", value, evidence)
    # Protect explicit control phrases from occasional omissions by the model.
    # These rules never supply an unstated profession, amount or geographic code.
    any_job=re.search(r"(?:люб(?:ая|ую)\s+работ[ау]|профессия\s+не\s*важна)",text,re.I)
    if not any_job:
        any_job=re.search(r'(?:люб\w*|разн\w*)\s+(?:работ\w*|должност\w*|професси\w*)|'
                          r'(?:професси\w*|должност\w*)(?:\s+\w+){0,4}\s+не\s+(?:выбира\w*|огранич\w*)',text,re.I)
    if any_job: result["query"]=field("any",None,any_job[0])
    if key(text) in {"не знаю кем работать","не знаю какую работу искать"}:
        result["query"]=field("unknown",None,text.strip())
    no_housing=re.search(r"(?:(?:больше\s+)?не\s+нуж\w*\s+(?:жиль[её]|проживание|общежитие)|(?:жиль[её]|проживание|общежитие)\s+(?:больше\s+)?не\s+нуж\w*)",text,re.I)
    housing=re.search(r"(?:(?<!не )с\s+(?:жиль[её]м|проживанием)|нуж\w*\s+(?:жиль[её]|проживание|общежитие))",text,re.I)
    if no_housing: result["housing"]=field("any",None,no_housing[0])
    elif housing: result["housing"]=field("known",True,housing[0])
    if key(text)=="удаленно": result["schedule"]=field("known",text.strip(),text.strip())
    child_ages=list(re.finditer(r"(?:сыну|сына|дочери|дочке|реб[её]нку)\s+(\d{1,3})",text,re.I))
    if len(child_ages)==1 and 1<=int(child_ages[0][1])<=100:
        result["age"]=field("known",int(child_ages[0][1]),child_ages[0][0])
    employment=result.get("employment")
    if employment and "рабочий день" in key(employment["evidence"]) and "занятост" not in key(employment["evidence"]):
        day=re.search(r"(?:неполный|полный)\s+рабочий\s+день",employment["evidence"],re.I)
        if day:
            result.pop("employment")
            result["schedule"]=field("known",day[0],day[0])
    return result


def assess(job, values, place):
    """Return an explicit rejection or the conditions not proved by source metadata."""
    unknown = []
    requirements = getattr(job, 'requirements', None) or ''
    if isinstance(values.get('age'), int) and values['age'] < 18:
        education = key(' '.join(filter(None, [job.education, requirements])))
        if re.search(r'высш\w*|средн\w*\s+профессиональн\w*|профессиональн\w*\s+образован', education):
            return "Требуется профессиональное образование", []
        experience = key(job.experience or '')
        required_years = getattr(job, 'experience_min_years', None)
        if required_years is None:
            match = re.search(r'\d+', experience)
            required_years = int(match[0]) if match else None
        if required_years is not None and required_years > 0:
            return "Требуется опыт работы", []
        if re.search(r'опыт\w*(?:\s+работы)?\s*(?:от|не менее)\s*[1-9]\d*\s*(?:лет|года|год)', key(requirements)):
            return "Требуется опыт работы", []
    if place:
        if place["kind"] == "city":
            # City, address and region are independent source claims. One cannot
            # override a contradictory populated claim in another field.
            if job.region and place.get('region') and not same_words(place['region'],job.region):
                return "Противоречие региона места работы", []
            for stated in re.findall(r'(?:^|[.;])\s*место\s+работы\s*:\s*([^.;]+)', requirements, re.I):
                named = resolve(stated.strip())
                if named and not any(same_words(place['name'], p['name']) for p in named):
                    return "Противоречие места работы в требованиях", []
            if job.city and job.address:
                named=resolve(job.address)
                head=job.address.split(',',1)[0].strip()
                if not named and head: named=resolve(head)
                explicit=re.search(r'(?:^|[,;])\s*(?:г\.?|город|село|деревня|пос[её]лок)\s+([^,;]+)',job.address,re.I)
                if named and not any(same_words(place['name'],p['name']) for p in named):
                    return "Противоречие города и адреса", []
                if explicit and not same_words(place['name'],explicit[1]):
                    return "Противоречие города и адреса", []
            address = key(job.city or job.address or "")
            if not address: unknown.append("место работы не указано")
            elif not re.search(r"(?<!\w)"+re.escape(key(place["name"]))+r"(?!\w)", address):
                if job.city or re.search(r"\b(?:г|город|п|поселок|село|деревня)\b", address): return "Другой город", []
                unknown.append("город не подтверждён адресом")
        elif not job.region: unknown.append("регион в карточке не указан")
        elif not same_words(place["name"], job.region): return "Другой регион", []
    salary = values.get("salary")
    if isinstance(salary,dict):
        reason,missing=money.assess(job,salary)
        if reason:return reason,[]
        if missing:unknown.append(missing)
    elif salary is not None and getattr(job,'source',None)=='hh' and job.salary_period!='month':
        unknown.append("месячная зарплата в рублях не подтверждена")
    elif salary is not None:
        if job.salary_to is not None and job.salary_to < salary: return "Зарплата ниже выбранной", []
        if job.salary_from is None: unknown.append("нижняя граница зарплаты не указана")
        elif job.salary_from < salary: unknown.append("нижняя граница зарплаты ниже желаемой")
    experience = values.get("experience")
    if experience is not None:
        raw = key(job.experience or "")
        match = re.search(r"\d+", raw)
        if getattr(job,'experience_min_years',None) is not None: required = job.experience_min_years
        elif re.search(r"без опыта|не требуется", raw): required = 0
        else: required = int(match[0]) if match else None
        if required is None: unknown.append("требуемый опыт не указан")
        elif required > experience: return "Требуется больший опыт", []
    education = values.get("education_level")
    if education:
        raw = key(job.education or "")
        required = "higher" if "высш" in raw else "vocational" if "профессиональ" in raw else "general" if "общ" in raw else None
        levels = {"no_professional":0, "general":1, "vocational":2, "higher":3}
        if not required and not re.search(r"не требуется|без образован|не предъявляются", raw): unknown.append("требования к образованию не подтверждены")
        elif required and education == "unspecified": unknown.append("нужно сопоставить уровень образования")
        elif required=="general" and education=="no_professional": unknown.append("нужно уточнить общее образование")
        elif required and levels[required] > levels.get(education, 0): return "Требуется другой уровень образования", []
    employment = values.get("employment")
    if employment:
        raw = key(job.employment or "")
        actual = "part_time" if re.search(r"частич|неполн",raw) else "full_time" if "полн" in raw else "temporary" if "временн" in raw else None
        if actual is None: unknown.append("занятость не подтверждена")
        elif actual != employment: return "Другой тип занятости", []
    schedule = values.get("schedule")
    if schedule:
        formats=set(getattr(job,'work_formats',()))
        if (re.search(r'удален|дистанцион',key(schedule)) and formats
                and formats <= {'ON_SITE','HYBRID','FIELD_WORK'}):
            return "Формат вакансии не допускает полностью удалённую работу", []
        details=key(' '.join(filter(None,[getattr(job,'requirements',None),getattr(job,'responsibilities',None)])))
        if re.search(r'удален|дистанцион',key(schedule)) and re.search(
            r'(?:удален\w*|дистанцион\w*)\s+(?:работ\w*|режим\w*)?\s*(?:не\s+предусмотр\w*|нет|невозмож\w*)|'
            r'(?:не\s+предусмотр\w*|нет)\s+(?:удален\w*|дистанцион\w*)|'
            r'исключительно\s+(?:в\s+цехе|на\s+месте|в\s+офисе)',details):
            return "Удалённая работа исключена текстом вакансии", []
        ratios=lambda text: {re.sub(r'\s','',v) for v in re.findall(r'\b\d+\s*/\s*\d+\b',text)}
        wanted_ratios,actual_ratios=ratios(schedule),ratios(job.schedule or '')
        if wanted_ratios and actual_ratios and wanted_ratios.isdisjoint(actual_ratios):
            return "Другой график", []
        def category(text):
            text=key(text)
            for code, pattern in (("part",r"неполн"),("full",r"полн.*день"),("shift",r"смен"),("flex",r"гибк"),("remote",r"удален")):
                if re.search(pattern,text): return code
            return None
        wanted, actual = category(schedule), category(job.schedule or "")
        if key(schedule) == key(job.schedule or ""): pass
        elif wanted_ratios:
            if not wanted_ratios.issubset(actual_ratios):unknown.append("точный график не подтверждён")
            elif re.sub(r'\d+\s*/\s*\d+|график|сменный|смены|только|\s','',schedule,flags=re.I):
                unknown.append("дополнительные условия графика не подтверждены")
        elif wanted and actual and wanted == actual:
            if not same_words(schedule,job.schedule or ''):unknown.append("точный график не подтверждён")
        elif wanted and actual and wanted != "remote" and actual != "remote": return "Другой график", []
        else: unknown.append("желаемый график не подтверждён")
    if values.get("housing"):
        details=key(' '.join(filter(None,[getattr(job,'requirements',None),getattr(job,'responsibilities',None)])))
        if re.search(r'не\s+(?:предоставл\w*|располага\w*|обеспечива\w*).{0,60}(?:жиль\w*|проживан\w*|общежити\w*)|'
                     r'(?:жиль\w*|проживан\w*|общежити\w*).{0,20}не\s+предоставл\w*',details):
            return "Жильё исключено текстом вакансии", []
        if job.accommodation is False: return "Жильё не предоставляется", []
        if job.accommodation is None: unknown.append("проживание в карточке не подтверждено")
    age=values.get('age')
    if type(age) is int:
        requirements=key(getattr(job,'requirements',None) or '')
        lower=re.search(r'возраст\w*\s*(?:от|не менее|старше)\s*(\d{1,3})',requirements)
        upper=re.search(r'возраст\w*\s*(?:до|не старше)\s*(\d{1,3})',requirements)
        if (lower and age<int(lower[1])) or (upper and age>int(upper[1])):
            return "Возраст не соответствует указанному требованию", []
        if not lower and not upper:unknown.append("возрастные условия работодателя не указаны")
    return None, unknown


class WorkBranch:
    def __init__(self, owner): self.owner = owner

    def state(self, s):
        if not s.work:
            s.work = dict(fields={name:field() for name in FIELDS}, place=None, choices=[],
                          awaiting=None, age_asked=False, scanned=0, nonce=secrets.token_hex(4), issue=None)
        return s.work

    def say(self, name, s, extra=None, buttons=None):
        result=self.owner.say("work_"+name, {**s.values, **(extra or {})}, buttons)
        if name=='results':result['text']=result['text'].replace('в «Работе России»','в источниках вакансий')
        return result

    def button(self, title, action, s):
        return callback(title, f"w:{self.owner.revision}:{self.state(s)['nonce']}:{action}")

    def reset_search(self, s):
        s.offset, s.buffer, s.seen = 0, [], set()
        self.state(s).update(scanned=0, nonce=secrets.token_hex(4))
        self.state(s).pop('source_offsets',None)
        self.state(s).pop('failed_sources',None)

    def question(self,s,node):
        state=self.state(s);name=node["field"]
        if name=="age":
            state["awaiting"]=None
            return node["next"]
        if name=="query":
            node={**node, **{key:re.sub(r'(?:его )?возраст(?: ребёнка)?,\s*','',node[key])
                            for key in ('text','adult_text') if key in node}}
        item=state["fields"].get(name,field())
        if item["status"] in {"known","any"}: return node["next"]
        if name=="query" and (state.get("started") or any(i["status"]!="unknown" for i in state["fields"].values())):
            return node["next"]
        buttons=self.owner.node_buttons(node,s)
        if name=="city":
            choices=[self.button(label(p),f"place:{i}",s) for i,p in enumerate(state["choices"][:20])]
            if not choices: choices=[self.button(self.owner.text("work_country_button"),"country",s)]
            buttons=choices+buttons
            state["awaiting"]="city"
            if state.get("unresolved"):
                return [self.say("place_choices" if state["choices"] else "place_unknown",s,
                                 {"place":state["unresolved"],"conditions":self.summary(s)},buttons)]
        elif name!="query" and not node.get("required") and item["status"]!="clarify":
            return node["next"]
        state["awaiting"]=name
        return [message((self.summary(s)+"\n\n" if name!="query" else "")+self.owner.render_text(node,s),buttons)]

    def summary(self, s):
        lines=[]
        for name,item in self.state(s)["fields"].items():
            if item["status"] == "unknown": continue
            value=item["value"]
            if item["status"] == "any": value="неважно" if name!="city" else "по всей России"
            elif item["status"] == "declined": value="не указан"
            elif item["status"] == "clarify": value="нужно уточнить"
            elif name in ENUMS: value=ENUMS[name].get(value,value)
            elif name=="housing": value="нужно жильё"
            elif name=="salary": value=money.display(value) if isinstance(value,dict) else f"от {value:,} ₽".replace(","," ")
            elif isinstance(value,list): value='; '.join(value)
            lines.append(f"{FIELDS[name]}: {value}")
        return "\n".join(lines)

    def apply(self, s, raw, text):
        state=self.state(s)
        updates=validate_fields(raw,text,s.values.get("role"))
        rejected={k:v for k,v in raw.items() if k in FIELDS and k not in updates and isinstance(v,dict) and v.get("subject")!="parent"} if isinstance(raw,dict) else {}
        state["parse_issue"]=any(v.get("status")=="known" and v.get("value") is not None for v in rejected.values())
        if rejected: self.owner.record(s,"work_rejected",fields=sorted(rejected),purpose="Значения модели без достаточного основания не применены")
        for name,item in updates.items():
            state["fields"][name]=item
            s.values[name]=item["value"] if item["status"]=="known" else None
            if name=="age": state["age_asked"]=True
        if "city" in updates:
            item=updates["city"]
            old_place=state.get("unresolved")
            state.update(place=None, choices=[])
            s.values.pop("region_code",None)
            if item["status"] == "known":
                name, sep, region=item["value"].partition(",")
                options=resolve(name.strip(),region.strip() if sep else None)
                if old_place and len(options)==1 and options[0]["kind"]=="region" and not re.search(r"вс[еяю]|целом",key(text)):
                    options=resolve(old_place,options[0]["name"])
                    name=old_place
                state["unresolved"]=name.strip()
                if len(options)==1: self.select_place(s,options[0])
                else:
                    state["choices"]=options
                    item["status"]="clarify"
                    s.values["city"]=None
            elif item["status"]=="any": state["unresolved"]=None
        if updates:
            state["started"]=True
            self.reset_search(s)
        self.owner.record(s,"work_conditions",statuses={k:v["status"] for k,v in state["fields"].items()},updated=list(updates),purpose="Проверенные условия текущего диалога; без записи переписки в базу")
        return bool(updates)

    def select_place(self,s,place):
        state=self.state(s)
        state.update(place=place,choices=[],unresolved=None)
        state["fields"]["city"]=field("known",place["name"],place["name"])
        s.values.update(city=place["name"],region_code=place["code"],region_name=place["region"])

    async def routed_input(self,s,text,node):
        deterministic_started=time.perf_counter()
        deterministic=deterministic_fields(text)
        fast_path=deterministic_complete(text,deterministic)
        deterministic_ms=(time.perf_counter()-deterministic_started)*1000
        llm_ms=0.0
        if fast_path:
            routed=dict(branch='work',work_fields={},values={},work_action=None,work_other=None)
            failed=False
        else:
            llm_started=time.perf_counter()
            try:
                routed=await self.owner.route(s,text,node)
                failed=False
            except (LLMError,LLMConfigurationError) as error:
                if not deterministic or (isinstance(error,LLMError) and error.code in {'invalid_response','invalid_json','invalid_branch'}):raise
                if isinstance(error,LLMConfigurationError):self.owner.record(s,'llm_error',code='configuration')
                routed=dict(branch='work',work_fields={},values={},work_action=None)
                failed=True
            llm_ms=(time.perf_counter()-llm_started)*1000
        if deterministic:
            routed['branch']='work'
        if routed.get('branch')=='work':
            raw=routed.get('work_fields') if isinstance(routed.get('work_fields'),dict) else {}
            llm_fields=[name for name,item in raw.items() if item is not None]
            raw=dict(raw)
            for name,item in deterministic.items():
                if raw.get(name) is None:raw[name]=item
            routed['work_fields']=raw
            method=('deterministic' if fast_path else
                    'deterministic_fallback' if deterministic and (failed or not llm_fields) else
                    'llm' if llm_fields else 'clarification')
            self.owner.record(s,'condition_recognition',branch='work',method=method,
                              recognition_method=method,
                              fields=[name for name,item in raw.items() if item is not None],
                              deterministic_ms=deterministic_ms,llm_ms=llm_ms,
                              llm_call_count=0 if fast_path else 1,
                              location_call_count=1 if raw.get('city') is not None else 0)
        return routed

    async def accept(self,user,s,routed,text):
        state=self.state(s)
        was_work=s.branch=="work"
        role=routed.get("values",{}).get("role")
        if isinstance(role,str) and role in {"child","parent"}:
            if re.search(r"родитель|опекун|сын|доч|реб[её]н|отец|мать|мама|папа",text,re.I): s.values["role"]=role
        if re.search(r"сыну|дочери|дочке|реб[её]нку",text,re.I) and re.search(r"работ|ваканс|ищем|ищу",text,re.I):
            s.values["role"]="parent"
        changed=self.apply(s,routed.get("work_fields",{}),text)
        s.branch="work"
        if state.get("parse_issue"): return [self.say("parse_error",s,buttons=[self.owner.menu_button()])]
        if routed.get("work_action")=="remind" and re.search(r"напом|завтра|потом|позже",text,re.I):
            if self.owner.child_reminders_hidden(s):
                return [self.say("not_understood",s,buttons=[self.owner.menu_button()])]
            return [self.say("remind_offer",s,buttons=[self.button(self.owner.text("button_new_reminder"),"remind",s),self.owner.menu_button()])]
        if not changed and routed.get("work_action")!="search":
            if not was_work and not routed.get("work_fields"):
                return await self.owner.enter(user,s,self.owner.branches["work"]["entry"])
            return [self.say("not_understood",s,buttons=[self.owner.menu_button()])]
        s.changing=False
        return await self.advance(user,s)

    async def input(self,user,s,text):
        state=self.state(s)
        # Only entire, unambiguous answers bypass the model; compound messages always go to LLM.
        clean=key(text)
        awaiting=state["awaiting"]
        if awaiting=="age" and (re.fullmatch(r"\d{1,3}(?: лет| года| год)?",clean) or clean in {"не знаю","не хочу указывать","не скажу","пропустить"}):
            if clean.startswith("не знаю"): item=field("unknown",None,text)
            elif not clean[0].isdigit(): item=field("declined",None,"не хочу указывать")
            else:
                age=int(re.search(r"\d+",clean)[0])
                if not 1<=age<=100: return [self.say("clarify",s,{"field":FIELDS["age"]})]
                item=field("known",age,text)
            state["fields"]["age"]=item; s.values["age"]=item["value"]
            state["parse_issue"]=False
            state["awaiting"]=None
            self.reset_search(s)
            return await self.advance(user,s)
        if awaiting=="city" and resolve(text):
            self.apply(s,{"city":field("known",text,text)},text)
            return await self.advance(user,s)
        node=dict(self.owner.nodes.get(s.node,{}),field=awaiting)
        routed=await self.routed_input(s,text,node)
        if routed.get("branch")=="work": return await self.accept(user,s,routed,text)
        if routed.get("branch")=="crisis": return [self.owner.say("crisis")]
        if routed.get("branch")=="off_topic": return [self.owner.say("unknown",buttons=[self.owner.menu_button()])]
        s.values.update(routed.get("values",{}))
        return await self.owner.enter(user,s,self.owner.branches[routed["branch"]]["entry"])

    async def enter(self,user,s,n):
        state=self.state(s)
        s.node,s.branch=n["id"],"work"
        action=n.get("action")
        if action in {"search_change","search_miss"}:
            s.changing=True
            if action=="search_miss": s.misses+=1
            buttons=[self.button(self.owner.text("button_more"),"more",s)] if s.offset is not None or s.buffer else []
            result=[self.say("change",s,{"conditions":self.summary(s)},buttons+[self.owner.menu_button()])]
            if s.misses>=self.owner.config["search"]["hh_after"]: result.insert(0,message(SEARCH_ALTERNATIVES,format="markdown"))
            return result
        if action in {"search","search_more"}:
            problem=self.problem(s)
            if problem: return problem
            # Even a direct action cannot silently broaden unconfirmed geography.
            for name in ("city",):
                item=state["fields"][name]
                if item["status"] not in {"known","any"}:
                    node=next((q for q in self.owner.config["nodes"] if q.get("branch")=="work" and q.get("field")==name),None)
                    if node: return await self.owner.enter(user,s,node["id"])
                    return [self.say("clarify",s,{"field":FIELDS[name]})]
            age=state["fields"]["age"]
            if age["status"]=="known" and age["value"]<self.owner.config["search"]["min_age"]:
                return await self.owner.enter(user,s,self.owner.bound("career"))
            state["awaiting"]=None
            return await self.search(s)
        return await self.owner.action(user,s,n)

    async def advance(self,user,s):
        problem=self.problem(s)
        if problem: return problem
        return await self.owner.enter(user,s,self.owner.branches["work"]["entry"])

    def problem(self,s):
        state=self.state(s)
        if state.get("parse_issue"): return [self.say("parse_error",s,buttons=[self.owner.menu_button()])]
        for name,item in state["fields"].items():
            if name not in {"city","age"} and item["status"]=="clarify":
                state["awaiting"]=name
                return [self.say("clarify",s,{"field":FIELDS[name],"conditions":self.summary(s)})]
        age=state["fields"]["age"]
        experience=state["fields"]["experience"]
        if age["status"]==experience["status"]=="known" and experience["value"]>age["value"]:
            state["fields"]["experience"]=field("clarify",None,experience["evidence"])
            s.values["experience"]=None;state["awaiting"]="experience"
            return [self.say("clarify",s,{"field":FIELDS["experience"],"conditions":self.summary(s)})]
        return None

    async def dynamic(self,user,s,parts):
        state=self.state(s)
        if len(parts)<4 or parts[1]!=str(self.owner.revision) or parts[2]!=state["nonce"]: return [self.owner.say("stale")]
        action=parts[3]
        if action=="place" and len(parts)==5 and parts[4].isdigit() and int(parts[4])<len(state["choices"]):
            self.select_place(s,state["choices"][int(parts[4])]); self.reset_search(s)
            return await self.advance(user,s)
        if action=="country":
            self.apply(s,{"city":field("any",None,"по всей России")},"по всей России")
            return await self.advance(user,s)
        if action=="age_skip":
            state["fields"]["age"]=field("declined");s.values["age"]=None;state["age_asked"]=True
            self.reset_search(s)
            return await self.advance(user,s)
        if action=="more": return await self.advance(user,s)
        if action=="remind": return self.owner.new_reminder(s,"Посмотреть вакансии")
        return [self.owner.say("stale")]

    async def search(self,s):
        from . import flow  # Shared seam for integration tests and the production adapter.
        state=self.state(s); settings=self.owner.config["search"]
        values={k:v["value"] for k,v in state["fields"].items() if v["status"]=="known"}
        place=state["place"]
        parameters=dict(region_code=place["code"] if place else None,experience_to=values.get("experience"),
                        accommodation=True if values.get("housing") else None,limit=100)
        scanned=0; excluded=[]
        try:
            if not s.buffer and s.offset is not None:
                source_started=time.perf_counter()
                page=await self.fetch(s,values.get("query"),offset=s.offset,**parameters)
                state['failed_sources']=getattr(page,'failed_sources',())
                source_ms=(time.perf_counter()-source_started)*1000
                filter_started=time.perf_counter()
                s.offset=page.next_offset;scanned+=1;state["scanned"]+=len(page.items)
                for job in page.items:
                    identity=(getattr(job,'source','trudvsem'),job.id)
                    if identity in s.seen: continue
                    s.seen.add(identity)
                    reason,missing=self.assess_job(s,job,values,place)
                    if reason:
                        if len(excluded)<100: excluded.append(reason)
                    else: s.buffer.append((job,missing))
                filter_ms=(time.perf_counter()-filter_started)*1000
        except TrudvsemError as error:
            self.owner.record(s,"search_error",code=error.code)
            return [message('Сейчас не удалось получить вакансии. Условия сохранены — можно попробовать ещё раз.'), message(SEARCH_ALTERNATIVES,
                    [self.button(self.owner.text("button_retry"),"more",s),self.owner.menu_button(contents=True)],format="markdown")]
        s.buffer.sort(key=lambda row:bool(row[1]))
        selected,s.buffer=s.buffer[:settings["page_size"]],s.buffer[settings["page_size"]:]
        more=s.offset is not None or bool(s.buffer)
        self.owner.record(s,"search",parameter_names=sorted({"query",*parameters}),condition_names=sorted(values),
                          fetched=state["scanned"],fetched_page=len(page.items) if scanned else 0,
                          shown=len(selected),excluded_count=len(excluded),excluded_reasons=sorted(set(excluded)),
                          next_page=s.offset,source_call_count=scanned,
                          source_search_ms=source_ms if scanned else 0.0,
                          filter_render_ms=filter_ms if scanned else 0.0)
        result=[self.say("results",s,{"conditions":self.summary(s),"scanned":state["scanned"]})]
        if state.get('failed_sources'):
            names={'hh':'HeadHunter','trudvsem':'«Работа России»'}
            failed=', '.join(names[n] for n in state['failed_sources'])
            result.append(message(f'Не удалось получить эту порцию из {failed}. Показываю доступные результаты. При следующем запросе повторю попытку; условия сохранены.'))
        if scanned and getattr(page,"stale_age_seconds",None) is not None:
            age=max(1,(page.stale_age_seconds+59)//60)
            result.append(message(f"Источник вакансий сейчас недоступен. Показываю сохранённые результаты примерно {age} мин. давности — перед откликом проверь вакансию по ссылке."))
        age=values.get("age")
        if age is not None and age<18:
            result.append(message('Я подберу варианты без требований к высшему образованию и большому опыту. Подходит ли вакансия по возрасту, лучше уточнить у работодателя.'))
            result.append(self.owner.say("minor"))
        if not selected:
            result.append(self.say("empty_more" if more else "empty_end",s))
            result.append(message(SEARCH_ALTERNATIVES,format="markdown"))
        previous=None
        for job,missing in selected:
            group="unverified" if missing else "verified"
            if group!=previous: result.append(self.say(group,s));previous=group
            salary=self.owner.text("salary_unknown") if job.salary_from is None and job.salary_to is None else self.owner.text("salary_known",dict(salary_from=job.salary_from if job.salary_from is not None else "не указано",salary_to=job.salary_to if job.salary_to is not None else "не указано"))
            if getattr(job,'source',None)=='hh' and (job.salary_from is not None or job.salary_to is not None):
                period={'month':'за месяц','hour':'за час','shift':'за смену','day':'за день','week':'за неделю','rotation':'за вахту'}.get(job.salary_period,'период не указан')
                salary+='; '+period+{'net':', на руки','gross':', до вычета налогов'}.get(job.salary_tax,', налоги не уточнены')
            text=self.owner.text("job",dict(title=job.title,company=job.company,address=job.address or job.city or job.region or "Место не указано",salary=salary,experience=job.experience or "не указан",housing=self.owner.text("housing_yes" if job.accommodation is True else "work_housing_absent" if job.accommodation is False else "housing_no")))
            text+=f"\nОбразование: {job.education or 'не указано'}\nЗанятость: {job.employment or 'не указана'}\nГрафик: {job.schedule or 'не указан'}"
            text+='\nИсточник: '+('HeadHunter' if getattr(job,'source',None)=='hh' else 'Работа России')
            if missing: text+="\nНе подтверждено: "+"; ".join(missing)+"."
            s.result_cards.append(dict(index=len(result), title=job.title, status=group,
                price=salary, subtitle=job.company, warnings='Не подтверждено: '+ '; '.join(missing) if missing else ''))
            result.append(message(text,[link(self.owner.text("button_open_job"),job.url)]))
        control=self.owner.nodes[self.owner.bound("work_search")]
        buttons=[]
        for button in self.owner.node_buttons(control,s):
            payload=button.get("payload","")
            index=int(payload.rsplit(":",1)[-1]) if payload.startswith("g:") else None
            target=self.owner.nodes.get(control["buttons"][index].get("target"),{}) if index is not None else {}
            if target.get("action")=="search_more" and not more: continue
            buttons.append(button)
        if buttons: result.append(self.say("controls",s,buttons=buttons))
        return result

    async def fetch(self,s,query,**parameters):
        from . import flow
        state=self.state(s)
        page=await flow.search_vacancies(query,place=state.get('place'),
            source_offsets=state.setdefault('source_offsets',{}),**parameters)
        if getattr(page,"attempt_count",1)>1:self.owner.record(s,"source_retry",code="transient")
        if getattr(page,"stale_age_seconds",None) is not None:
            self.owner.record(s,"source_cache_fallback",age_seconds=page.stale_age_seconds)
        return page

    def assess_job(self,s,job,values,place):return assess(job,values,place)
