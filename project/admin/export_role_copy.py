"""Audit navigation and export a frozen draft into three editorial Markdown files."""

import ast
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import re

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.flow import FlowDialogue, PreviewReminders, matches
from project.llm.services.help_points import HelpPointsBranch, catalog
from project.llm.services.scenario import validate

OUT = ROOT / 'docs/role-copy-2026-09-27'
ROLES = {'child': ('01-rebenok.md', 'Я ребёнок', 'menu'),
         'parent': ('02-roditel.md', 'Я родитель / опекун', 'pa_home'),
         'candidate': ('03-budushchiy-roditel.md', 'Хочу принять ребёнка в семью', 'family')}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def allowed(conditions, role):
    return all(matches(r, {'role': role}) for r in conditions if r.get('field') == 'role')


def visible(button, role, nodes, branches):
    if not allowed(button.get('conditions', []), role):
        return False
    chosen = button.get('values', {}).get('role', role)
    if chosen != role:
        return False
    target = nodes.get(button.get('target'))
    return not target or role in branches[target['branch']]['roles']


def graph(config, role):
    nodes = {n['id']: n for n in config['nodes']}
    branches = {b['id']: b for b in config['branches']}
    paths = {config['start']: ['Выбор роли']}
    queue = deque(paths)
    while queue:
        key = queue.popleft()
        node = nodes[key]
        routes = [r for r in node.get('routes', []) if allowed([r], role)]
        forced = next((r for r in routes if r.get('field') == 'role'), None)
        edges = [(forced['target'], 'автоматический переход')] if forced else [
            (b['target'], b['label']) for b in node.get('buttons', [])
            if b.get('target') and visible(b, role, nodes, branches)
            and not b.get('values', {}).get('navigation')]
        if not forced:
            edges += [(r['target'], 'условный переход') for r in routes]
            if node.get('next'):
                edges.append((node['next'], 'продолжение'))
        for target, label in edges:
            if target in paths or target not in nodes or role not in branches[nodes[target]['branch']]['roles']:
                continue
            paths[target] = paths[key] + [label]
            queue.append(target)
    return paths


def audit(config):
    nodes = {n['id']: n for n in config['nodes']}
    branches = {b['id']: b for b in config['branches']}
    broken, empty, self_links, thin = [], [], [], []
    for node in nodes.values():
        for index, b in enumerate(node.get('buttons', [])):
            identity = dict(node=node['id'], index=index, label=b['label'], target=b.get('target'))
            if not b.get('url') and b.get('target') not in nodes:
                broken.append(identity)
            if b.get('target') == node['id'] and not b.get('values'):
                self_links.append(identity)
        if node['kind'] == 'message' and node['id'] not in {'rental', 'hp_city'}:
            substantive = any(not b.get('values', {}).get('navigation') for b in node.get('buttons', []))
            if not any([node.get('text', '').strip(), node.get('adult_text', '').strip(), node.get('tasks'),
                        node.get('sources'), substantive, node.get('next'), node.get('routes')]):
                empty.append(node['id'])
            if node.get('content_status') == 'incomplete' or re.search(
                    'ещё (?:не подготовлен|проверя)|[Пп]роверенн[^\n]*пока нет|ещё нужно проверить', node.get('text', '')):
                thin.append(dict(node=node['id'], has_tasks=bool(node.get('tasks')),
                                 incoming=[dict(node=p['id'], label=b['label']) for p in nodes.values()
                                           for b in p.get('buttons', []) if b.get('target') == node['id']]))
    return dict(buttons=sum(len(n.get('buttons', [])) for n in nodes.values()),
                broken=broken, empty=empty, self_links=self_links, incomplete=thin,
                reachable={r: list(graph(config, r)) for r in ROLES}, removed=[])


def source_strings(path):
    """Lossless Cyrillic literal inventory, excluding system prompts/docstrings."""
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    rows = []

    def walk(node, scope='module'):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = node.name
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return  # docstring
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and (t.id == 'CONTRACT' or 'prompt' in t.id.lower()) for t in targets):
                return
        if isinstance(node, ast.JoinedStr):
            text = ''.join(part.value if isinstance(part, ast.Constant) else '{' + ast.unparse(part.value) + '}'
                           for part in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        else:
            for child in ast.iter_child_nodes(node):
                walk(child, scope)
            return
        if re.search('[А-Яа-яЁё]', text):
            rows.append(dict(line=node.lineno, function=scope, text=text))
    walk(tree)
    return rows


def block(text):
    return '\n```text\n' + text + '\n```\n'


def export(state):
    config = state['config']
    validate(config)
    report = audit(config)
    # Removal is deliberately restricted to proven empty/broken buttons. This snapshot has none.
    if report['broken'] or report['empty'] or report['self_links']:
        raise RuntimeError('Review confirmed navigation defects before exporting the final snapshot.')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'scenario-snapshot.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    nodes = {n['id']: n for n in config['nodes']}
    branches = {b['id']: b for b in config['branches']}
    engine = FlowDialogue(PreviewReminders(), config, state['revision'])
    sources = ['flow.py', 'work.py', 'free_work.py', 'rental.py', 'help_points.py',
               'money.py', 'geography.py', 'condition_coverage.py']
    literals = {name: source_strings(ROOT / 'project/llm/services' / name) for name in sources}
    report['config_sha256'] = digest(config)
    report['revision'] = state['revision']
    report['published_id'] = state['published_id']
    report['source_sha256'] = {name: hashlib.sha256((ROOT / 'project/llm/services' / name).read_bytes()).hexdigest() for name in sources}
    report['files'] = {}
    for role, (filename, title, home) in ROLES.items():
        paths = graph(config, role)
        eligible = [n for n in config['nodes'] if role in branches[n['branch']]['roles']]
        active = [nodes[k] for k in paths]
        inactive = [n for n in eligible if n['id'] not in paths]
        lines = [f'# Тексты бота: {title}', '',
                 f"Снимок локального черновика **revision {state['revision']}**, выгружен {datetime.now(timezone.utc).isoformat()}. "
                 f"Опубликованная версия MAX: {state['published_id']}; это выгрузка черновика, не подтверждение текстов на VPS.", '',
                 f"SHA-256 конфигурации: `{report['config_sha256']}`.", '',
                 '## Как читать и редактировать', '',
                 'Основная часть содержит экраны, доступные этой роли из меню. Текст в блоке — точная формулировка '
                 'для этой роли, без редакторских исправлений. Чек-листы и подписи кнопок включены. '
                 'Путь указан как один из возможных; таблица кнопок содержит остальные переходы.', '',
                 'Сохраняйте ID экрана, имя поля и ключ шаблона: по ним можно будет внести согласованную редактуру. '
                 'Эти Markdown-файлы пока не импортируются автоматически. Общие поля влияют на несколько ролей; '
                 'изменения одного такого поля в трёх файлах позднее нужно согласовать.', '',
                 'В конце отдельно сохранены недоступные из меню экраны, служебные шаблоны и строковый реестр кода. '
                 'Реестр содержит также слова распознавания и проверки: это не всё самостоятельные реплики. '
                 'Инструкции модели, исходные исследования и выключенные материалы базы знаний не являются текстом кнопочных ответов.', '',
                 'Фигурные скобки обозначают подстановку. Названия вакансий, цены, города, условия пользователя и '
                 'ответы внешних источников меняются при поиске; выгружены их шаблоны, а не выдуманные результаты.', '',
                 '## Итог проверки кнопок', '',
                 f"В конфигурации {report['buttons']} кнопок. Отсутствующих целей, переходов в полностью пустой экран "
                 'и кнопок, которые без действия ведут в тот же экран, не найдено. Ничего не удалено. '
                 'Пустое поле текста у поискового действия не означает пустой ответ: его формирует движок.', '',
                 'Проверка охватывает структуру переходов, доступность по роли и наполнение. '
                 'Доступность всех внешних сайтов и живых поисковых источников этим аудитом не подтверждается.', '',
                 '## Места для редакторского внимания', '']
        for row in report['incomplete']:
            if row['node'] in paths:
                lines.append(f"- `{row['node']}` — {nodes[row['node']]['title']}; "
                             + ('есть чек-лист; ' if row['has_tasks'] else '') + 'текст сообщает о неполноте сведений, сохранён для редактуры.')
        if 'career_test' in paths:
            lines += ['- `career_test`: кнопка «Профориентационный тест» открывает памятку, но в ней нет ссылки на тест. '
                      'Это несоответствие ожиданию от названия, а не пустой экран. '
                      'Отдельный вход в профориентацию из `work_entry` уже содержит ссылку; старую памятку стоит переработать.']
        lines += ['', '## Оглавление экранов', '']
        lines += [f"- [{n['title']}](#screen-{n['id']}) — `{n['id']}`" for n in active]
        for heading, collection in [('Доступные экраны', active), ('Архив: не доступны из меню этой роли', inactive)]:
            lines += ['', '## ' + heading, '']
            for n in collection:
                key = 'adult_text' if role != 'child' and n.get('adult_text') else 'text'
                lines += [f"<a id=\"screen-{n['id']}\"></a>", f"### {n['title']} · `{n['id']}`", '',
                          f"Поле для правки: `nodes[id={n['id']}].{key}`. Тип: `{n['kind']}`; раздел: `{n['branch']}`.", '',
                          'Путь: ' + ' → '.join(paths.get(n['id'], ['Нет пути из меню этой роли. Не показывается обычной навигацией.'])), '']
                if n.get('routes'):
                    lines += ['Автоматические переходы: ' + json.dumps(n['routes'], ensure_ascii=False), '']
                dynamic = n['kind'] == 'action' or n['id'] in {'rental', 'hp_city'}
                if dynamic:
                    lines += ['**Динамический экран:** ответ формирует поисковая ветка/действие `' + str(n.get('action') or n['branch']) + '`. '
                              'Ниже сохранено поле сценария; фактические реплики и кнопки также см. в шаблонах и реестре кода.', '']
                if n.get(key):
                    lines.append(block(n[key]))
                else:
                    lines += ['Поле текста пустое; это действие движка, а не пустая кнопка.', '']
                if n.get('tasks'):
                    lines += [f"Чек-лист — `nodes[id={n['id']}].tasks`: ", '']
                    lines += [f'- [ ] {t}' for t in n['tasks']]
                lines += ['', '| Кнопка | Переход / действие | Поле |', '|---|---|---|']
                details = []
                for i, b in enumerate(n.get('buttons', [])):
                    # The common role picker intentionally shows all three roles.
                    if n['id'] != config['start'] and not visible(b, role, nodes, branches):
                        continue
                    nav = b.get('values', {}).get('navigation')
                    target = ('Главное меню роли: `' + home + '`') if nav == 'contents' else (
                        'Предыдущий открытый экран; запасной: `' + b.get('target', '') + '`') if nav == 'back' else b.get('url') or '`' + b.get('target', '') + '`'
                    lines.append(f"| {b['label'].replace('|', '&#124;')} | {target} | `buttons[{i}].label` |")
                    values = {k: v for k, v in b.get('values', {}).items() if k != 'navigation'}
                    if values:
                        details.append(f"Значения кнопки `{i}`: `{json.dumps(values, ensure_ascii=False)}`.")
                lines += ['', *details, '']
                if n.get('next'):
                    lines += ['', 'Следующий шаг: `' + n['next'] + '` (после ответа/по кнопке продолжения).']
                for source in n.get('sources', []):
                    item = config['sources'][source]
                    lines += ['', f"Источник-кнопка `{source}`: [{item['title']}]({item['url']})."]
        lines += ['', '## Служебные шаблоны и подписи', '',
                  'Полный набор `ui` сохранён для проверки полноты. Наличие ключа не означает, что он сейчас вызывается. '
                  'Напоминания выключены; их оставшиеся подписи не видны пользователю. '
                  'Шаблоны `work_*` относятся к работе, `rental_*` — к жилью. '
                  'Если соответствующая ветка отсутствует в оглавлении роли, эти шаблоны для неё неактивны.', '']
        for key, item in config['ui'].items():
            lines += [f'### Шаблон `{key}`', '', f"Поле: `ui.{key}.text`; скрыт: {bool(item.get('hidden'))}; переход: `{item.get('target') or 'задаётся кодом'}`.", block(item.get('text', ''))]
        lines += ['', '## Динамические формулировки и строковый реестр движка', '',
                  'Эти тексты не хранятся в `nodes`/`ui`. Для каждого указан файл, функция и строка исходного снимка. '
                  'Фрагменты могут соединяться в одну реплику; строки с регулярными выражениями и короткие слова '
                  'также могут служить распознаванию ввода. Не заменяйте их автоматически как обычный ответ. '
                  'Включены общие и резервные пути движка; недоступная роли ветка не начинает работать от наличия текста в этом приложении.', '']
        for name, rows in literals.items():
            if name == 'help_points.py' and role != 'child':
                continue
            lines += [f'### `project/llm/services/{name}`', '']
            for row in rows:
                lines += [f"**`{name}:{row['line']}` · `{row['function']}`**", block(row['text'])]
        if role == 'child':
            lines += ['', '## Карточки подготовленного справочника помощи', '',
                      'Точные тексты, которые текущий код собирает из локального справочника. '
                      'Это снимок данных, а не повторная проверка организаций. Внешний справочник при подключении '
                      'может вернуть иные данные. Одинаковые варианты одной карточки объединены.', '']
            helper = HelpPointsBranch(engine)
            for index, point in enumerate(catalog()):
                variants = {}
                for category in point['categories']:
                    card = helper.card(point, category)
                    variants.setdefault(card['text'], []).append(category)
                for text, categories in variants.items():
                    lines += [f"### Точка {index}: {point['name']}", '',
                              f"Источник: `project/llm/data/help_points.json[{index}]`; виды помощи: {', '.join(categories)}.", block(text)]
                    if point.get('url'):
                        lines += [f"Кнопка: [Посмотреть информацию]({point['url']}).", '']
        content = '\n'.join(lines) + '\n'
        (OUT / filename).write_text(content, encoding='utf-8')
        assert all(n.get('adult_text' if role != 'child' and n.get('adult_text') else 'text', '') in content for n in eligible)
        assert all(item.get('text', '') in content for item in config['ui'].values())
        report['files'][role] = dict(file=filename, active=len(active), inactive=len(inactive), chars=len(content))
    (OUT / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['revision', 'buttons', 'broken', 'empty', 'self_links', 'files']}, ensure_ascii=False))


if __name__ == '__main__':
    export(request('state'))
