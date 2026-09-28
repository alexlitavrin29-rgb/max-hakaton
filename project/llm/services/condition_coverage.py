"""Conservative domain guards for constraints omitted or truncated by extraction.

These identify a field to clarify, never invent its value. This is a bounded
vocabulary of pilot capabilities, not a second free-text semantic parser.
"""
import re

RENTAL_EXTRA = r'залог\w*|депозит\w*|комисси\w*|коммун\w*|\bжк[ух]\b|район\w*|метро|животн\w*|кошк\w*|кот\w*|собак\w*|питом\w*|комнат\w*|\bдом\w*|куп\w*|покуп\w*|посуточ\w*|сутк\w*|\bсуток\b|этаж\w*|мебел\w*|ремонт\w*'
OTHER_TASK = r'купить|покуп\w*|посуточ\w*|сутк\w*|\bсуток\b|\b(?:комната|комнату|коттедж\w*)\b|\bдом\b(?!\s*(?:№\s*)?\d)'
TOTAL_COST = r'(?:вместе|включая|с\s+уч[её]том)\s+(?:с\s+)?коммун\w*|со\s+все\w*\s+расход\w*'
WORK_EXTRA = r'дмс|страховк\w*|питани\w*|оформлен\w*|официальн\w*|\bтк\s+рф\b|рядом\s+с|развоз\w*'
WORK_CUES = {
    'age':r'(?:мне|сыну|дочери|реб[её]нку)\s+\d{1,3}\b',
    'education_level':r'образован\w*|диплом\w*|колледж\w*|техникум\w*|вуз\w*',
    'experience':r'опыт\w*|стаж\w*',
    'employment':r'подработ\w*|занятост\w*',
    'schedule':r'график\w*|\d+\s*/\s*\d+|удал[её]н\w*',
    'salary':r'зарплат\w*|оплат\w*|тысяч\w*|рубл\w*|\d+\s*к\b',
    'housing':r'жиль\w*|проживан\w*|общежит\w*',
}

def rental_extra(text):
    result=[]
    for clause in re.split(r'[;.!?]',text):
        clause=clause.strip()
        if not clause:continue
        if re.search(r'не\s+условие\s+поиска|не\s+прибавка\s+к',clause,re.I):continue
        if re.search(OTHER_TASK,clause,re.I) and re.search(r'\b(?:не\s+(?:нуж\w*|планир\w*|ищ\w*)|ничего\s+не\s+ищ\w*)\b',clause,re.I):
            continue
        if re.search(RENTAL_EXTRA,clause,re.I):
            if re.search(r'\b(?:нуж\w*|хочу|важн\w*|без|только|ищ\w*|сним\w*|покуп\w*)\b',clause,re.I) or re.search(
                r'\b(?:залог\w*|комисси\w*)\s*(?:до\s+\d|не\s+больше|максимум)',clause,re.I):
                result.append(clause)
        elif re.search(r'^(?:дополнительно|ещ[её]|также|важн\w*|нуж\w*|хочу)\b',clause,re.I) and not re.search(r'аренд\w*|квартир\w*|сним\w*',clause,re.I):
            result.append(clause)
    return result


def other_task_requested(text):
    for clause in re.split(r'[;.!?]',text):
        if not re.search(OTHER_TASK,clause,re.I):continue
        if re.search(r'\b(?:не\s+(?:нуж\w*|планир\w*|ищ\w*)|ничего\s+не\s+ищ\w*)\b',clause,re.I):continue
        return True
    return False


def update_wishes(existing, text, additions):
    """Keep independent wishes so removing one does not discard its neighbours."""
    wishes=list(existing)
    removals=[]
    for clause in re.split(r'[;.!?]',text):
        if re.search(r'\b(?:больше\s+не\s+(?:нуж\w*|важн\w*)|отменя\w*|снима\w*|не\s+применя\w*)\b',clause,re.I):
            removals.append(clause)
    common={'нужн','важн','больш','прове','поиск','аренд','работ','хочу','требо','самой','самим'}
    def terms(value):
        return {word[:4] for word in re.findall(r'[а-яё]{4,}',value.casefold()) if word[:4] not in common}
    for removal in removals:
        tokens=terms(removal)
        wishes=[wish for wish in wishes if not (tokens & terms(wish))]
    for addition in additions:
        if not isinstance(addition,str):continue
        if re.search(r'\bоставля\w*\s+пожелани\w*',addition,re.I) and terms(addition) & set().union(*(terms(old) for old in wishes)):
            continue
        addition=addition.strip(' .;')
        addition=re.sub(r'^(?:дополнительно\s+хотелось\s+бы|ещ[её]\s+хочу|важны|нужны|нужен|нужна|хочу)\s*:?\s*','',addition,flags=re.I)
        parts=re.split(r'\s*,\s*|\s+и\s+',addition)
        for index,part in enumerate(parts):
            part=part.strip(' .;')
            if index and parts[0].lstrip().startswith('без ') and not part.startswith('без '):part='без '+part
            if re.search(r'\b(?:разреша\w*|соглас\w*|не\s+проверя\w*)\b',part,re.I):continue
            if part and not any(terms(part)==terms(old) for old in wishes):wishes.append(part)
    return wishes


def consent_to_unchecked(text):
    """An affirmative search instruction explicitly acknowledging unchecked wishes."""
    if re.search(r'\b(?:не\s+(?:начина\w*|ищ\w*|соглас\w*|разреша\w*)|пока\s+не|передумал\w*|отказ\w*)\b',text,re.I):
        return False
    approval=re.search(r'\b(?:соглас\w*|разреша\w*|можно\s+начина\w*|начина\w*|запуска\w*|устраива\w*|даю\s+согласие)\b',text,re.I)
    scope=re.search(r'\b(?:без\s+(?:(?:его|е[её]|их|этого|дополнительной)\s+)?провер\w*|не\s+провер\w*|не\s+подтвержд\w*|провер\w*\s+нельзя|по\s+(?:городу|доступным|условиям)|пожелани\w*\s+.*не\s+провер\w*)\b',text,re.I)
    return bool(approval and scope)

def missing_work(raw,text,role,applicant):
    """Past pay/adult facts don't create desired constraints. Named null fields do."""
    result={}
    for name,pattern in WORK_CUES.items():
        if isinstance(raw.get(name),dict):continue
        for clause in re.split(r'[;.!?]|,|\bтеперь\b',text,flags=re.I):
            if not re.search(pattern,clause,re.I):continue
            if name=='salary' and re.search(r'раньше|прежде|получал\w*|платили',clause,re.I):continue
            if role=='parent' and applicant!='self' and re.search(r'\bмне\b|\bмой\b|\bя\b',clause,re.I) and not re.search(r'сын|доч|реб[её]н',clause,re.I):continue
            result[name]=clause.strip();break
    return result
