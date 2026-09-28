from datetime import datetime, timezone

from project.llm.services.flow import FlowSession
from project.llm.services.session_codec import decode_flow, encode_flow


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
