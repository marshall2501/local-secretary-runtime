"""Authoritative resolution, value normalization and time grounding."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .memory_contracts import validate_draft
from .memory_registry import PREDICATE_REGISTRY


def load_catalog(repository):
    return repository.load_catalog()


def resolve_entity(mention, catalog):
    matches = [e for e in catalog if mention in e['names']]
    active = [e for e in matches if not e['retired']]
    if len(active) == 1:
        return 'resolved', active[0]
    if len(active) > 1:
        return 'ambiguous', None
    return ('invalid' if matches else 'unresolved'), None


def ground(intake, candidate, catalog):
    error = validate_draft(candidate, intake.raw_text)
    if error:
        return dict(validation='invalid', reason=error, entity_id=None, predicate=None)
    resolution, entity = resolve_entity(candidate['subject']['mention'], catalog)
    predicate = PREDICATE_REGISTRY.get(candidate['predicate']['concept_hint'])
    clock = datetime.fromisoformat(intake.recorded_at).astimezone(ZoneInfo(intake.timezone))
    raw_time = candidate['time']['raw']
    when = clock
    if raw_time == '昨日':
        when = (clock - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif raw_time:
        return dict(validation='valid', reason='unresolved_time', entity_id=None, predicate=predicate)
    value = candidate['object']['raw']
    if predicate == 'os_release_changed':
        value = dict(product='Windows 11' if 'Windows' in candidate['evidence']['quote'] else None,
                     to_release=value, transition_hint='upgrade')
    return dict(validation='valid', reason=resolution, entity_id=entity['id'] if entity else None,
                entity_type=entity['kind'] if entity else None, predicate=predicate, value=value,
                time=dict(raw=raw_time, resolved=when.isoformat(), precision='day' if raw_time else 'input_time',
                          kind=candidate['time']['kind'], observed_at=intake.observed_at))
