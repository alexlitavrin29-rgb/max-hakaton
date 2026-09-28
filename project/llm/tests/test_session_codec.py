from datetime import datetime, timezone

from project.llm.services.flow import FlowSession
from project.llm.services.session_codec import decode_flow, encode_flow
from project.llm.services.vacancies import Vacancy, VacancyContact
import json


def test_flow_state_round_trip_and_version_rejection():
    state = FlowSession(node='work_search', history=['menu', 'work'],
                        values={'role': 'child', 'city': 'Казань'},
                        work={'fields': {'query': {'status': 'known', 'value': 'повар'}},
                              'streams': [{'offset': 100}]},
                        pending={'stage': 'confirm', 'due': datetime(2026, 9, 30, tzinfo=timezone.utc)},
                        done={1, 3}, seen={'one', 'two'})
    encoded = encode_flow(state)
    assert encoded['version'] == 1
    assert 'touched' not in encoded['fields']
    restored = decode_flow(encoded)
    assert restored.history == ['menu', 'work']
    assert restored.work['streams'][0]['offset'] == 100
    assert restored.done == {1, 3} and restored.seen == {'one', 'two'}
    assert restored.pending['due'] == datetime(2026, 9, 30, tzinfo=timezone.utc)
    encoded['version'] = 2
    try:
        decode_flow(encoded)
    except ValueError:
        pass
    else:
        assert False, 'Unknown state schema accepted'


def test_work_page_round_trip_keeps_remaining_vacancies_and_seen_ids():
    vacancy = Vacancy(id='42', source='hh', title='Медбрат', company='Тест',
                      salary_from=None, salary_to=None, region='Москва', city='Москва',
                      address=None, experience=None, accommodation=None,
                      requirements=None, responsibilities=None, url='https://example.org/42',
                      contacts=(VacancyContact(kind='phone', value='123'),),
                      work_formats=('remote',))
    state = FlowSession(buffer=[(vacancy, ['опыт'])], seen={('hh', '42')})
    restored = decode_flow(json.loads(json.dumps(encode_flow(state), ensure_ascii=False)))
    assert restored.buffer == [(vacancy, ['опыт'])]
    assert restored.seen == {('hh', '42')}
