"""Versioned input envelope and untrusted extractor draft contract."""
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MemoryIntake:
    contract_version: str
    input_id: str
    raw_text: str
    source_kind: str
    source_ref: str
    recorded_at: str
    confidentiality: str = 'private'
    timezone: str = 'Asia/Tokyo'
    observed_at: str | None = None

    @classmethod
    def issue(cls, raw_text, *, now=None, timezone='Asia/Tokyo'):
        identifier = str(uuid4())
        return cls('memory-intake/v1', identifier, raw_text, 'user_statement',
                   'fixture://memory-intake/' + identifier,
                   (now or datetime.now(ZoneInfo(timezone))).isoformat(), timezone=timezone)

    def validate(self):
        UUID(self.input_id)
        if self.contract_version != 'memory-intake/v1':
            raise ValueError('unsupported_contract')
        if not isinstance(self.raw_text, str) or not self.raw_text.strip() or len(self.raw_text) > 20000:
            raise ValueError('invalid_input_text')
        if self.source_kind not in {'user_statement', 'file', 'web', 'tool', 'service'}:
            raise ValueError('invalid_source_kind')
        if not self.source_ref.startswith('fixture://'):
            raise ValueError('fictional_source_required')
        if self.confidentiality not in {'public', 'private', 'restricted'}:
            raise ValueError('invalid_confidentiality')
        ZoneInfo(self.timezone)
        for value in (self.recorded_at, self.observed_at):
            if value is not None and datetime.fromisoformat(value).utcoffset() is None:
                raise ValueError('timezone_required')

    def payload_hash(self):
        return sha256(json.dumps(asdict(self), ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')).encode()).hexdigest()


DRAFT_FIELDS = {'candidate_id', 'operation_hint', 'content_class', 'semantic_kind_hint',
                'subject', 'predicate', 'object', 'time', 'modality', 'polarity', 'basis',
                'evidence', 'retention_hint'}


def validate_draft(draft, text):
    """Reject extra authority fields and forged/misaligned evidence."""
    if not isinstance(draft, dict) or set(draft) != DRAFT_FIELDS:
        return 'invalid_draft_fields'
    enums = {
        'operation_hint': {'assert', 'correct', 'retract'},
        'content_class': {'fact', 'observation', 'preference', 'hypothesis', 'decision', 'plan', 'issue'},
        'semantic_kind_hint': {'attribute', 'event', 'state', 'relation', None},
        'modality': {'asserted', 'probable', 'possible', 'intended', 'conditional'},
        'polarity': {'positive', 'negative'},
        'basis': {'explicit_user_statement', 'tool_observation', 'source_extraction', 'model_inference'},
        'retention_hint': {'durable', 'task_only', 'unknown'},
    }
    if any(not isinstance(draft[k], (str, type(None))) or draft[k] not in choices
           for k, choices in enums.items()):
        return 'invalid_draft_enum'
    for key, fields in {'subject': {'mention', 'type_hint'}, 'predicate': {'concept_hint'},
                        'object': {'kind', 'raw', 'normalized_hint'}, 'time': {'raw', 'kind'},
                        'evidence': {'start', 'end', 'quote'}}.items():
        if not isinstance(draft[key], dict) or set(draft[key]) != fields:
            return 'invalid_nested_fields'
    if not isinstance(draft['candidate_id'], str) or not draft['candidate_id'] or len(draft['candidate_id']) > 80:
        return 'invalid_candidate_id'
    for value in (draft['subject']['mention'], draft['predicate']['concept_hint'],
                  draft['object']['raw'], draft['object']['kind'], draft['time']['kind']):
        if not isinstance(value, str):
            return 'invalid_draft_value'
    if draft['time']['raw'] is not None and not isinstance(draft['time']['raw'], str):
        return 'invalid_time'
    e = draft['evidence']
    if type(e['start']) is not int or type(e['end']) is not int or not isinstance(e['quote'], str):
        return 'invalid_evidence'
    if not (0 <= e['start'] < e['end'] <= len(text)) or text[e['start']:e['end']] != e['quote']:
        return 'invalid_evidence'
    if draft['subject']['mention'] and draft['subject']['mention'] not in e['quote']:
        return 'ungrounded_subject'
    return None
