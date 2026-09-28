"""Readable workbooks for editing reply bodies, without navigation or source code."""
import ast
import json
import re

from project.admin.documents_reference_draft import ROOT, request
from project.admin.export_role_copy import ROLES, graph, digest

OUT = ROOT / 'docs/message-editing-2026-09-27'


def dynamic_messages(filename):
    tree = ast.parse((ROOT / 'project/llm/services' / filename).read_text(encoding='utf-8-sig'))
    result = []

    def wording(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return wording(node.left) + wording(node.right)
        if isinstance(node, ast.JoinedStr):
            return ''.join(p.value if isinstance(p, ast.Constant) else '{данные}' for p in node.values)
        return '{данные}'

    for node in ast.walk(tree):
        values = []
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'message' and node.args:
            values = [node.args[0]]
        if isinstance(node, ast.Dict):
            values += [v for k, v in zip(node.keys, node.values)
                       if isinstance(k, ast.Constant) and k.value == 'question']
        if isinstance(node, ast.Call):
            values += [k.value for k in node.keywords if k.arg == 'question']
        for value in values:
            text = wording(value)
            if re.search('[А-Яа-яЁё]', text):
                result.append((text, dict(file=filename, line=value.lineno, expression=ast.unparse(value))))
    return result


def main():
    state = request('state')
    config = state['config']
    nodes = {n['id']: n for n in config['nodes']}
    OUT.mkdir(parents=True, exist_ok=True)
    mapping = dict(revision=state['revision'], sha256=digest(config), roles={})
    common = {'stale', 'too_long', 'llm_error', 'unsupported', 'prepared_only', 'unknown', 'crisis', 'invalid'}
    work_extra = {'job', 'empty', 'minor', 'schedule', 'housing_no', 'housing_yes', 'salary_known',
                  'salary_unknown', 'search_error', 'search_change', 'search_header'}
    for role, (filename, title, _) in ROLES.items():
        paths = graph(config, role)
        branches = {nodes[k]['branch'] for k in paths}
        entries = []

        def add(title, context, text, origin):
            if text.strip():
                entries.append(dict(title=title, context=context, text=text, origin=origin))

        for key in paths:
            n = nodes[key]
            # An unconditional role redirect has no displayed message.
            if any(r.get('field') == 'role' and r.get('value') == role and r.get('op') == 'eq'
                   for r in n.get('routes', [])):
                continue
            if n['kind'] == 'action' or key == 'hp_city':
                continue
            field = 'adult_text' if role != 'child' and n.get('adult_text') else 'text'
            text = n.get(field, '')
            if n.get('tasks'):
                text += '\n\n' + '\n'.join('☐ ' + t for t in n['tasks'])
            context = ' → '.join(p for p in paths[key] if p != 'автоматический переход')
            if key == config['start']:
                context = 'Первое открытие бота, до выбора роли.'
            add(n['title'], context, text, dict(node=key, field=field, tasks=n.get('tasks', [])))

        for key, item in config['ui'].items():
            if key.startswith('button_') or key == 'work_country_button' or item.get('hidden'):
                continue
            group = 'Общие ответы бота'
            include = key in common
            if 'work' in branches and (key.startswith('work_') or key in work_extra):
                include, group = True, 'Поиск работы: уточнения, результаты и сообщения об ошибках'
            if 'rental' in branches and key.startswith('rental_'):
                include, group = True, 'Поиск жилья: уточнения, результаты и сообщения об ошибках'
            if role == 'child' and key == 'work_parent_age':
                include = False
            if role == 'parent' and key == 'work_age_question':
                include = False
            if include:
                first = re.sub(r'\{[^}]+\}', '', item['text']).strip().split('\n')[0]
                add(first[:85] or 'Сообщение с результатами', group, item['text'], dict(ui=key))

        files = []
        if 'work' in branches:
            files += [('work.py', 'Поиск работы'), ('free_work.py', 'Поиск работы')]
        if 'rental' in branches:
            files += [('rental.py', 'Поиск жилья')]
        if 'help_points' in branches:
            files += [('help_points.py', 'Где могут помочь')]
        seen = {e['text'] for e in entries}
        for source, group in files:
            for text, origin in dynamic_messages(source):
                if text in seen:
                    continue
                seen.add(text)
                add('Дополнительное сообщение: ' + group.lower(),
                    group + '. Сообщение появляется в зависимости от ответа человека или результата поиска.', text, origin)

        lines = [f'# Сообщения бота: «{title}»', '',
                 '**Рабочий файл для редактирования ответов бота.** Здесь меняем только то, что бот пишет человеку.', '',
                 f"Основа: локальный черновик {state['revision']}. Тексты пока сохранены в исходном виде.", '',
                 '## Как работать с файлом', '',
                 '1. Найдите сообщение по пути или заголовку.',
                 '2. Прочитайте блок «Сейчас бот пишет».',
                 '3. Под заголовком «Моя редакция» напишите сообщение так, как хотите его видеть в боте. '
                 'Если оставляете ответ прежним, ничего туда не вписывайте.', '',
                 'Названия в пути — ориентиры: они помогают понять, после какого нажатия появляется ответ. '
                 'Редактировать названия кнопок здесь не нужно. Номера сообщений оставьте — по ним я перенесу ваши правки.', '',
                 'Можно менять вступление, объяснение, формулировки шагов и текст рядом со ссылкой. '
                 'Ссылки и важные условия сохраняйте, если не хотите отдельно менять их. '
                 'Для желаемого тона: тепло и понятно, без канцелярита и сюсюканья.', '',
                 'В поисковых сообщениях фигурные скобки обозначают место для города, суммы, условий или результата. '
                 'В дополнительных ответах `{данные}` — переменная часть. '
                 'Сначала идут сообщения экранов, затем уточнения и ответы поиска. '
                 'Повторяющиеся ответы на разных экранах оставлены отдельно, чтобы каждый можно было изменить.', '',
                 'Приветствие и некоторые ответы общие для нескольких ролей. После вашей редактуры я сверю такие места. '
                 'Изменение этого файла само по себе не меняет работающего бота.', '',
                 '## Список сообщений', '']
        index = []
        for i, entry in enumerate(entries, 1):
            code = f'{role.upper()}-{i:03d}'
            entry['code'] = code
            lines += [f"- [{code}. {entry['title']}](#{code.lower()})"]
        for entry in entries:
            code = entry['code']
            lines += ['', '---', '', f'<a id="{code.lower()}"></a>',
                      f"## {code}. {entry['title']}", '', '**Когда появляется:** ' + entry['context'], '',
                      '**Сейчас бот пишет:**', '']
            lines += ['> ' + line for line in entry['text'].split('\n')]
            lines += ['', '### Моя редакция', '', '<!-- Напишите новый текст здесь. Пустой блок означает: оставить прежний ответ. -->', '']
            index.append(dict(code=code, original=entry['text'], origin=entry['origin']))
        (OUT / filename).write_text('\n'.join(lines) + '\n', encoding='utf-8')
        mapping['roles'][role] = index
    (OUT / 'message-map.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'scenario-snapshot.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({role: len(rows) for role, rows in mapping['roles'].items()}))


if __name__ == '__main__':
    main()
