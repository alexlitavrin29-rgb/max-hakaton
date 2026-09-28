"""Presentation adapter for the published bot engine, with independent sessions."""
from copy import deepcopy
from urllib.parse import urlparse

from project.llm.channels.max import callback, link, message
from project.llm.services.flow import FlowDialogue, PreviewReminders

SECTIONS = {'work': 'Ищу работу', 'rental': 'Ищу жильё', 'help': 'Вам тут помогут'}
ENTRIES = {'work': 'work', 'rental': 'rental', 'help': 'help_points'}
BRANCHES = set(ENTRIES.values())


def buttons(reply):
    return [b for a in reply.get('attachments', []) if a.get('type') == 'inline_keyboard'
            for row in a['payload']['buttons'] for b in row]


def safe_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme in {'https', 'http'} and bool(parsed.netloc) and not parsed.username
    except (TypeError, ValueError):
        return False


def is_more(engine, payload):
    if payload.startswith('hp:more:') or (payload.startswith(('w:', 'r:')) and payload.endswith(':more')):
        return True
    if payload.startswith('g:'):
        try:
            _, _, node_id, index = payload.split(':')
            target = engine.nodes[node_id]['buttons'][int(index)].get('target')
            return engine.nodes.get(target, {}).get('action') == 'search_more'
        except (ValueError, KeyError, IndexError):
            return False
    return False


class MiniDialogue(FlowDialogue):
    def __init__(self, config, revision):
        # This is an in-memory channel view, never a write to the published scenario.
        config = deepcopy(config)
        config.get('rules', {}).pop('miniapp_search_bot', None)
        config.get('rules', {}).pop('miniapp_answers_bot', None)
        config['reminders_enabled'] = False
        for branch in config['branches']:
            branch['roles'] = list(set(branch.get('roles', [])) | {'user'})
        for node in config['nodes']:
            if node['branch'] == 'work':
                node['adult_text'] = node.get('text', '')
        super().__init__(PreviewReminders(), config, revision)
        self.session(1).values = {'role': 'user'}

    def menu_button(self, contents=False):
        return callback('К разделам', 'mini:home')

    def node_buttons(self, node, session):
        result = []
        for button in super().node_buttons(node, session):
            payload = button.get('payload', '')
            if payload.startswith('g:'):
                original = node['buttons'][int(payload.rsplit(':', 1)[1])]
                target = self.nodes.get(original.get('target'), {})
                if target.get('branch') not in BRANCHES:
                    if original.get('values', {}).get('navigation') == 'back':
                        result.append(self.menu_button())
                    elif target.get('id') == 'career':
                        source = self.config.get('sources', {}).get('career', {})
                        if safe_url(source.get('url')):
                            result.append(link('Пройти тест на профориентацию', source['url']))
                    continue
            # Do not offer reopening the same mini-app from its own screen.
            if '?startapp=' not in button.get('url', ''):
                result.append(button)
        return result

    async def enter(self, user, session, node_id):
        node = self.nodes.get(node_id, {})
        if node.get('branch') not in BRANCHES and node_id != 'career':
            return [message('Выберите раздел: работа, жильё или помощь.', [self.menu_button()])]
        if node.get('branch') == 'work' and self.work:
            self.work.state(session).setdefault('applicant', 'self')
        return await super().enter(user, session, node_id)


def present(engine, replies, section):
    session = engine.session(1)
    metadata = {c['index']: c for c in session.result_cards}
    cards, notices, controls = [], [], []
    offered = set()
    for index, reply in enumerate(replies):
        actions = []
        for button in buttons(reply):
            if button.get('type') == 'link' and safe_url(button.get('url')):
                actions.append(dict(label=button['text'], url=button['url']))
            elif button.get('type') == 'callback':
                payload = button['payload']
                # Returning to the bot's role menu is returning to the app's home.
                if payload == 'back' and not session.history:
                    payload = 'mini:home'
                actions.append(dict(label=button['text'], payload=payload, more=is_more(engine, payload)))
                offered.add(payload)
        item = dict(text=reply.get('text', ''), actions=actions)
        if index in metadata:
            cards.append(dict(**item, **{key: value for key, value in metadata[index].items() if key != 'index'}))
        else:
            text = item['text']
            if section == 'help' and text.startswith('Где могут помочь\n'):
                text = text.split('\n', 1)[1].lstrip()
            notices.append(dict(text=text))
            controls.extend(actions)
    summary = engine.work.summary(session) if section == 'work' and session.work else ''
    if section == 'rental' and session.rental.get('city'):
        st = session.rental
        summary = 'Город: ' + st['city']['name']
        if st.get('budget'):
            summary += '\nАренда: до ' + str(st['budget']) + ' ₽ в месяц'
        if st.get('other'):
            summary += '\nДополнительные пожелания: ' + st['other']
    return dict(section=section, cards=cards, notices=notices, controls=controls, summary=summary), offered
