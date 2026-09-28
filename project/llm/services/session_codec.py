"""Explicit, versioned JSON representation of a FlowSession."""

from dataclasses import fields
from datetime import datetime

from .flow import FlowSession

VERSION = 1
SET_FIELDS = {'done', 'seen'}


def encode_flow(session):
    values = {}
    for item in fields(FlowSession):
        if item.name == 'touched':
            continue
        value = getattr(session, item.name)
        if item.name in SET_FIELDS:
            value = sorted(value)
        if item.name == 'pending' and value:
            value = {key: entry.isoformat() if isinstance(entry, datetime) else entry
                     for key, entry in value.items()}
        values[item.name] = value
    return {'version': VERSION, 'fields': values}


def decode_flow(document):
    if document.get('version') != VERSION or not isinstance(document.get('fields'), dict):
        raise ValueError('Unsupported dialogue state version')
    allowed = {item.name for item in fields(FlowSession)} - {'touched'}
    values = document['fields']
    if set(values) != allowed:
        raise ValueError('Incomplete dialogue state')
    values = {key: set(value) if key in SET_FIELDS else value for key, value in values.items()}
    if values['pending'] and isinstance(values['pending'].get('due'), str):
        values['pending'] = dict(values['pending'], due=datetime.fromisoformat(values['pending']['due']))
    return FlowSession(**values)
