"""Freeze the independent oracle before changing production code. Never used in prompts."""
import hashlib
import itertools
import json
from pathlib import Path

PLACES=[('Суздаль','Владимирская область','33'),('Кяхта','Бурятия','03'),('Таруса','Калужская область','40'),
('Аскат','Алтай Республика','04'),('Хужир','Иркутская область','38'),('Варзуга','Мурманская область','51'),
('Кинель-Черкассы','Самарская область','63'),('Багдарин','Бурятия','03'),('Дивеево','Нижегородская область','52'),
('Бакчар','Томская область','70'),('Ловозеро','Мурманская область','51'),('Усть-Кокса','Алтай Республика','04'),
('Плёс','Ивановская область','37'),('Кологрив','Костромская область','44'),('Нолинск','Кировская область','43'),
('Тотьма','Вологодская область','35'),('Сольвычегодск','Архангельская область','29'),('Мышкин','Ярославская область','76'),
('Лальск','Кировская область','43'),('Весьегонск','Тверская область','69'),('Нерехта','Костромская область','44'),
('Касли','Челябинская область','74'),('Чухлома','Костромская область','44'),('Нязепетровск','Челябинская область','74'),
('Пудож','Карелия','10'),('Кадуй','Вологодская область','35'),('Холм-Жирковский','Смоленская область','67'),
('Усть-Цильма','Коми','11'),('Седельниково','Омская область','55'),('Крутинка','Омская область','55')]
JOBS=['техник по защите информации','слесарь-инструментальщик 5 разряда','инженер по охране окружающей среды',
'оператор станков с программным управлением','рентгенолаборант','дефектоскопист','медицинский лабораторный техник',
'пекарь-кондитер','оператор котельной','техник-метеоролог','аппаратчик химводоочистки','ветеринарный фельдшер',
'монтажник радиоэлектронной аппаратуры','реставратор тканей','инженер-гидролог','корректор','дизайнер интерфейсов',
'разработчик встраиваемых систем','специалист по закупкам','электромеханик по лифтам','лаборант химического анализа',
'машинист холодильных установок','инструктор по адаптивной физической культуре','технолог пищевого производства',
'наладчик контрольно-измерительных приборов','контент-редактор','инженер по качеству','зубной техник',
'оператор очистных сооружений','помощник бурильщика']

def cases():
    result=[];orders=list(itertools.permutations(range(3)))
    for i in range(120):
        name,region,code=PLACES[i%30];job=JOBS[(i*7+i//30)%30];amount=43500+i*137
        kind=['min','max','range','target'][i%4];upper=amount+18000 if kind=='range' else amount if kind=='max' else None
        lower=None if kind=='max' else amount
        phrase={'min':f'не меньше {amount:,}'.replace(',',' '),'max':f'до {amount}',
                'range':f'от {amount} до {upper}','target':f'примерно {amount}'}[kind]+' рублей в месяц'
        parts=[f'Место поиска: {name}, {region}',f'Желаемая работа: {job}',f'По оплате: {phrase}']
        group=['geography','profession','salary','order','incremental','correction'][i//20]
        ordered=[parts[n] for n in orders[i%6]]
        messages=['Мне 26. '+'. '.join(ordered)]
        if group=='incremental': messages=['Мне 26. '+ordered[0],ordered[1],ordered[2]]
        if group=='order' and i%2: messages=['Мне 26. '+'. '.join(ordered[:2]),ordered[2]]
        if group=='profession':
            messages=[f'Раньше работал продавцом, учился на повара, но хочу другую работу: {job}. Мне 26. {parts[0]}. {parts[2]}']
        if group=='geography':
            messages=[f'Живу в Москве, а работу ищу здесь: {name}, {region}. Мне 26. {parts[1]}. {parts[2]}']
        if group=='salary':
            # Novel amounts and representation, not hand-picked accepted constants.
            phrase=f'от {amount//1000} тысяч {amount%1000} рублей в месяц'
            kind,lower,upper='min',amount,None
            messages=[f'Мне 26. {parts[0]}. {parts[1]}. Раньше платили 35000, теперь хочу {phrase}']
        if group=='correction':
            messages=[f'Мне 26. {parts[0]}. {parts[1]}. Хочу от 38000 рублей в месяц',
                      'Зарплата теперь неважна',f'Вернём требование к зарплате: {phrase}']
        expected={'city':name,'region_code':code+'0'*11,'query':job,'age':26,
                  'salary':dict(kind=kind,lower=lower,upper=upper,period='month',tax='unknown',currency='RUB')}
        result.append(dict(id=f'control-{i+1:03}',group=group,messages=messages,expected=expected,
                           rules=['no_invented_fields','preserve_previous','no_unnecessary_question','matching_api','honest_cards']))
    return result

if __name__=='__main__':
    p=Path('docs/test-results/work-free-input/control-v1.json');p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists(): raise SystemExit('Frozen control file already exists; do not overwrite')
    p.write_text(json.dumps(cases(),ensure_ascii=False,indent=2),encoding='utf-8')
    p.with_suffix('.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest(),encoding='ascii')
    print('Frozen 120 full dialogues; six groups; SHA256 recorded')
