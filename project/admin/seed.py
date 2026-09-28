"""One-time import of the prototype into editable product data."""

from copy import deepcopy

from project.llm.services.knowledge import CARDS, SOURCES, document_card


def initial_config():
    branches = [
        ("start", "Приветствие и роли"), ("work", "Работа"), ("education", "Учёба"),
        ("housing", "Жильё"), ("benefits", "Выплаты"), ("documents", "Документы"),
        ("money", "Деньги"), ("family", "Ребёнок и семья"), ("help", "Социальная помощь"),
        ("reminders", "Напоминания"), ("plan", "С чего начать"),
    ]
    config = dict(start="welcome", menu="menu", branches=[], nodes=[], sources=deepcopy(SOURCES),
                  materials=[], ui={}, evaluations=[], examples=[],
                  rules={"tone": "Тёплый, спокойный и уважительный. Ребёнку — на ты, взрослому — на вы. Без канцелярита.",
                         "length": "Короткое объяснение и 2–4 понятных действия. До 1000 символов.",
                         "forbidden": "Не отвечать на посторонние вопросы. Не выдумывать вакансии, выплаты, документы, контакты и ссылки.",
                         "clarify": "Спрашивать только то, что влияет на ответ. При нехватке материалов — уточняющий вопрос и официальный источник.",
                         "router": "Определи намерение и только явно сообщённые параметры. Возраст и работа в запросе родителя относятся к ребёнку.",
                         "judge": "Оцени полезность, соответствие запросу, отсутствие выдуманных фактов, тон и понятность следующего шага."},
                  search={"page_size": 5, "scan_pages": 3, "filter_city": True, "filter_salary": True,
                          "include_unknown_salary": True, "min_age": 16, "hh_after": 2})
    def button(label, target, **kwargs): return dict(label=label, target=target, **kwargs)
    def node(id, branch, title, text="", kind="message", **kwargs):
        n = dict(id=id, branch=branch, title=title, kind=kind, text=text, adult_text="", buttons=[],
                 next="", conditions=[], routes=[], tasks=[], sources=[], **{})
        n.update(kwargs); config["nodes"].append(n); return n
    def menu_button(): return button("Главное меню", "menu")
    for id, title in branches:
        config["branches"].append(dict(id=id, title=title, entry="welcome" if id=="start" else id,
                                       status="ready", roles=["child","parent","candidate"], prompt="", keywords=[]))
    node("welcome", "start", "Знакомство", "Привет! Я помогу разобраться с работой, учёбой, жильём и документами — и буду твоей точкой опоры.\n\nЯ бот для детей и выпускников детдомов, родителей и тех, кто хочет принять ребёнка в семью. Выбери роль или просто напиши, с чем помочь. Не присылай паспортные данные, адрес и телефон.", buttons=[
        button("Я ребёнок", "menu", values={"role":"child"}), button("Я родитель / опекун", "menu", values={"role":"parent"}), button("Хочу принять ребёнка в семью", "family", values={"role":"candidate"})])
    node("menu", "start", "Главное меню", "С чем помочь? Можно выбрать тему или написать своими словами.", adult_text="С чем помочь ребёнку? Выберите тему или напишите вопрос.", buttons=[
        button(title, id) for id,title in branches if id not in {"start","help","family"}]+[
        button("Ребёнку скоро 18", "adult", conditions=[dict(field="role",op="eq",value="parent")]),
        button("Принять ребёнка в семью", "family", conditions=[dict(field="role",op="ne",value="child")]), button("Начать заново", "welcome", values={"reset":True})])
    work_questions = [
        ("work","query","Расскажи, какую работу хочешь найти. Можно написать любые известные условия: например, только город или только профессию, возраст, опыт, график, пожелания по зарплате или проживанию. Необязательно указывать всё — начнём с того, что ты знаешь. Если чего-то не хватит для поиска, я уточню. Если профессия не важна, поищем разные варианты.","text",False,"work_city"),
        ("work_city","city","В каком городе или регионе будем искать?","city",True,"work_age"),
        ("work_age","age","Сколько лет соискателю? Это поможет учесть возраст при поиске.","number",True,"work_experience"),
        ("work_experience","experience","Есть опыт такой работы? Напиши количество лет или «без опыта». Можно пропустить.","number",False,"work_salary"),
        ("work_salary","salary","На какую зарплату рассчитываем? Можно написать сумму или «неважно».","number",False,"work_search")]
    for id,field,text,input_type,required,next_id in work_questions:
        node(id,"work",{"query":"Профессия","city":"Город","age":"Возраст","experience":"Опыт","salary":"Зарплата"}[field],text,"question",field=field,input_type=input_type,required=required,next=next_id,buttons=[menu_button()])
    config["nodes"][-5]["buttons"].insert(0,button("Пока не знаю, кем работать", "career"))
    node("work_search","work","Поиск вакансий",kind="action",action="search", routes=[dict(field="age",op="lt",value=16,target="career")], buttons=[button("Показать ещё","work_more"),button("Изменить условия","work_change"),button("Ничего не подошло","work_miss"),button("Напомнить посмотреть","reminder_new",values={"reminder_text":"Посмотреть вакансии"}),menu_button()])
    for id,action,title in [("work_more","search_more","Следующие вакансии"),("work_change","search_change","Изменение поиска"),("work_miss","search_miss","Ничего не подошло")]:
        node(id,"work",title,kind="action",action=action,next="work",buttons=[button("Выбрать профессию","career"),menu_button()])
    categories = {
        "education": ("С чего начнём?",[("Выбрать профессию","career"),("Поступление","admission"),("Особые условия и поддержка","education_support"),("Документы","docs_education")]),
        "housing": ("Что сейчас нужно узнать о жилье?",[("Положено ли жильё / очередь","housing_rights"),("Как проверить очередь","housing_rights"),("Жильё уже получено","received"),("Сейчас негде жить","help"),("Документы","docs_housing")]),
        "family": ("Помогу разобраться с устройством ребёнка в семью. Какой вопрос сейчас важнее?",[("Формы семейного устройства","forms"),("Порядок действий","family_steps"),("Документы ближайшего шага","docs_family"),("Информация о детях","children")]),
        "documents": ("Для чего нужны документы?",[("Выплаты","docs_benefits"),("Жильё","docs_housing"),("Поступление","docs_education"),("Принять ребёнка в семью","docs_family")]),
        "plan": ("Начнём с ближайшей задачи. Что хочется решить в первую очередь?",[("Жильё","housing"),("Работа","work"),("Деньги / поддержка","benefits"),("Учёба","education")])}
    for id,(text,choices) in categories.items():
        node(id,id,text.split('?')[0],text,buttons=[button(a,b) for a,b in choices]+[menu_button()])
    card_map = {"education":("admission","education"),"career":("career","education"),"education_support":("education_support","education"),"benefits":("benefits_result","benefits"),"housing":("housing_rights","housing"),"received":("received","housing"),"family":("family_steps","family"),"forms":("forms","family"),"children":("children","family"),"adult":("adult","benefits"),"money":("money","money"),"rejection":("rejection","benefits")}
    for key,(id,branch) in card_map.items():
        text,tasks,sources=CARDS[key]
        node(id,branch,{"admission":"Поступление","benefits_result":"Возможная поддержка","housing_rights":"Жильё и очередь","family_steps":"Порядок устройства","career":"Выбор профессии","money":"Повседневные деньги","forms":"Формы устройства","children":"Открытая информация о детях","adult":"Ребёнку скоро 18","rejection":"Если получен отказ","received":"После получения жилья","education_support":"Поддержка во время учёбы"}.get(id,id),text,tasks=list(tasks),sources=list(sources),buttons=[button("Мой чек-лист","checklist"),button("Поставить напоминание","reminder_tasks"),menu_button()])
        config["materials"].append(dict(id="material_"+id,title=config["nodes"][-1]["title"],text=text,branches=[branch],regions=[],roles=[],min_age=None,max_age=None,sources=list(sources),checked="2026-09-22",review_days=90,enabled=True))
    for topic in ("benefits","housing","education","family"):
        text,tasks,sources=document_card(topic)
        node("docs_"+topic,"documents","Документы: "+dict(branches)[topic],text,tasks=list(tasks),sources=list(sources),buttons=[button("Мой чек-лист","checklist"),button("Поставить напоминание","reminder_tasks"),menu_button()])
    for id,field,text,next_id,typ in [
        ("benefits","age","Сколько лет ребёнку или выпускнику, для которого ищем поддержку?","benefits_city","number"),
        ("benefits_city","home_city","В каком городе живёт ребёнок или выпускник?","benefits_study","city"),
        ("benefits_study","study","Учишься сейчас? Если да, где и очно ли?","benefits_family","text"),
        ("benefits_family","family_status","Какая ситуация сейчас: детдом, самостоятельная жизнь после выпуска, опека, приёмная семья или усыновление?","benefits_result","text")]:
        node(id,"benefits",{"age":"Возраст для поддержки","home_city":"Регион проживания","study":"Обучение","family_status":"Семейная ситуация"}[field],text,"question",field=field,input_type=typ,required=False,next=next_id,buttons=[menu_button()])
    next(n for n in config["nodes"] if n["id"]=="benefits_study")["adult_text"]="Ребёнок сейчас учится? Если да, где и очно ли?"
    node("help","help","Где нужна помощь","В каком городе нужна помощь сейчас?", "question",field="help_city",input_type="city",required=True,next="help_registry",buttons=[menu_button()])
    node("help_registry","help","Реестр помощи",kind="action",action="social",buttons=[menu_button()])
    for id,action,title,branch in [("checklist","checklist","Отметки чек-листа","documents"),("reminders","reminders","Мои напоминания","reminders"),("reminder_new","reminder_new","Создать напоминание","reminders"),("reminder_tasks","reminder_tasks","Напомнить о пункте","reminders")]:
        node(id,branch,title,kind="action",action=action,buttons=[menu_button()])
    # Every service response and generated button is editable too.
    texts = {
        "invalid":"Не получилось понять ответ. {hint}", "llm_error":"Сервис понимания сообщений сейчас не ответил. Можно продолжить кнопками или написать ещё раз.",
        "unknown":"Помогу с работой, учёбой, жильём, поддержкой и документами. Что сейчас нужно?",
        "crisis":"Если есть непосредственная опасность сейчас, нужно обратиться по номеру 112 и к безопасному человеку рядом. Я не могу вызвать помощь.",
        "empty":"В просмотренной части выдачи подходящих вакансий не нашлось. Можно продолжить поиск или изменить условия.",
        "search_header":"Ищем: {query}, {city}. Вакансии с портала «Работа России».",
        "search_error":"Портал вакансий сейчас не ответил. Условия сохранены — можно повторить поиск.",
        "job":"{title}\n{company}\n{address}\n{salary}\nОпыт: {experience}\n{housing}",
        "salary_known":"Зарплата: {salary_from} — {salary_to} ₽", "salary_unknown":"Зарплата не указана; соответствие желаемой сумме не подтверждено.",
        "housing_yes":"Жильё указано. Бесплатность и условия нужно уточнить.", "housing_no":"Жильё не подтверждено.",
        "minor":"Возрастные условия нужно уточнить у работодателя: источник не подтверждает, что эта вакансия подходит несовершеннолетнему.",
        "schedule":"Пожелание по графику: {schedule}. График нужно проверить в карточке вакансии.",
        "search_change":"Какие условия поменяем? Можно написать новую профессию, город, зарплату или все пожелания сразу.",
        "checklist":"Мой чек-лист:\n{tasks}", "no_tasks":"Сначала открой тему с чек-листом — или создай своё напоминание.",
        "reminder_tasks":"О каком невыполненном пункте напомнить?", "reminders":"Мои напоминания", "no_reminders":"Пока напоминаний нет. Можно создать своё.",
        "reminder_text":"О чём напомнить? Короткий текст без личных данных: он может быть виден на экране телефона.",
        "reminder_city":"В каком городе нужно напомнить? Это нужно для местного времени.",
        "reminder_date":"Город: {city}. Время местное ({zone}). Напиши время и дату: ЧЧ:ММ, ДД.ММ.ГГГГ. Например: {example}.",
        "reminder_confirm":"Напомнить: {text}\nКогда: {date}\nГород: {city} ({zone})?",
        "reminder_saved":"Сохранено. Напомню {date} ({city}).", "reminder_deleted":"Напоминание удалено.",
        "reminder_cancelled":"Создание или перенос напоминания отменены.", "reminder_invalid":"Нужны будущие дата и время: ЧЧ:ММ, ДД.ММ.ГГГГ.",
        "reminder_row":"{state} · {date} ({zone})\n{text}", "notification":"Напоминание: {text}",
        "stale":"Эта кнопка уже неактивна. Можно открыть нужный раздел заново.",
        "social_generic":"За срочной социальной помощью можно обратиться в соцзащиту по месту нахождения. Нужно уточнить, где доступны ночлег, питание, одежда или бытовая помощь. Наличие мест уточняется у организации.",
        "social_registry":"Официальный реестр Татарстана. В перечне услуг нужно найти временное размещение, питание, одежду или бытовую помощь. Условия приёма и наличие мест уточняются у организации.",
        "no_material":"Чтобы ответить точнее, нужно уточнить ситуацию. {question} Можно также свериться с источником по этой теме.",
        "too_long":"Сообщение слишком длинное. Можно описать один вопрос до 2500 символов.",
        "unsupported":"Пока я понимаю текст и кнопки. Можно написать вопрос словами.",
    }
    labels={"menu":"Главное меню","skip":"Не знаю / пропустить","continue":"Продолжить","open_job":"Открыть вакансию","retry":"Повторить поиск","more":"Показать ещё","change":"Изменить условия","new_reminder":"Создать своё","save":"Сохранить","cancel":"Отменить","edit_date":"Изменить дату","delete":"Удалить","snooze":"Напомнить позже","done":"Сделано","task":"{mark} {task}","rem_task":"{task}","reminder_list":"Мои напоминания","hh":"Посмотреть вакансии на HeadHunter"}
    config["ui"]={k:dict(text=v,category="message",hidden=False,target="") for k,v in texts.items()}
    config["ui"].update({"button_"+k:dict(text=v,category="button",hidden=False,target="") for k,v in labels.items()})
    config["evaluations"]=[dict(id="job_request",title="Работа без лишней анкеты",messages=["Мне 19, ищу работу грузчиком в Туле, без опыта, зарплата неважна"],criteria="Показаны реальные вакансии или честная ошибка портала. Не спрашивать повторно уже названные параметры.",good="Понятные карточки реальных вакансий и ссылка на оригинал.",bad="Сейчас не получилось разобрать сообщение."),dict(id="parent",title="Взрослый спрашивает о ребёнке",messages=["Я опекун. Ребёнку 17, живём в Туле, он учится в школе. Какие выплаты проверить?"],criteria="Возраст и поддержка относятся к ребёнку. Нет обещаний назначения конкретных выплат. Есть следующий шаг и источники.",good="В вашей ситуации стоит проверить…",bad="Вам точно положено 100000 рублей.")]
    config["bindings"]={k:k for k in ("work_age","career","work_search","reminders","reminder_tasks")}
    return config
