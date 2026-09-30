"""Bounded Japanese extractor. Unknown statements become review candidates.

This implementation needs no model. A future model may emit the same draft
contract, but policy independently checks the supported literal grammar.
"""
import re

OS = re.compile(r'(?P<subject>[^。、\n]+?)(?:のWindows\s*11)?を(?P<release>\d{2}H[12])に(?P<verb>上げた|更新した|した|してから|する予定)')
DRIVER = re.compile(r'(?P<subject>[^。、\n]+?)(?:の)?ドライバー?(?:を|も)(?:(?P<value>[A-Za-z0-9][A-Za-z0-9._-]*)に)?更新した')
SERVO = re.compile(r'(?P<subject>[^。、\n]+?)を(?P<value>[A-Za-z0-9][A-Za-z0-9._-]*)に(?:交換した|取り替えた)')
UNSAFE = ('かもしれない', 'らしい', 'なかった', 'していない', '予定ではない', 'もし', 'なら', '？', '?', '訂正', 'ではなく')


def draft(identifier, text, start, end, subject='', concept='unknown', value='',
          content_class='fact', modality='asserted', time_raw=None):
    return dict(candidate_id=identifier, operation_hint='assert', content_class=content_class,
                semantic_kind_hint='event' if concept in {'os.upgrade', 'driver.update', 'servo.replace'} else None,
                subject=dict(mention=subject, type_hint=None), predicate=dict(concept_hint=concept),
                object=dict(kind='text', raw=value, normalized_hint=None),
                time=dict(raw=time_raw, kind='occurred_at'), modality=modality,
                polarity='positive', basis='explicit_user_statement',
                evidence=dict(start=start, end=end, quote=text[start:end]), retention_hint='durable')


def extract(intake):
    result = []
    text = intake.raw_text
    if text.strip('。！! \n') in {'こんにちは', 'ありがとう', 'おはよう'}:
        return result
    for sentence in re.finditer(r'[^。\n]+[。]?', text):
        quote = sentence.group()
        start = sentence.start()
        match = OS.search(quote) or DRIVER.search(quote) or SERVO.search(quote)
        if match:
            subject = match['subject']
            prefix = quote[:match.start()].strip()
            time_raw = '昨日' if subject.startswith('昨日') or prefix in {'昨日、', '昨日'} else None
            if subject.startswith('昨日'):
                subject = subject[2:].lstrip('、 ')
            concept = 'os.upgrade' if 'release' in match.groupdict() else ('driver.update' if match.re is DRIVER else 'servo.replace')
            value = match.groupdict().get('release') or match.groupdict().get('value') or ''
            plan = match.groupdict().get('verb') == 'する予定'
            item = draft(str(len(result)+1), text, start, sentence.end(), subject, concept, value,
                         'plan' if plan else 'fact', 'intended' if plan else 'asserted', time_raw)
            if (prefix not in {'', '昨日、', '昨日'} or any(marker in quote for marker in UNSAFE)
                    or match.end() < len(quote.rstrip('。')) and match.groupdict().get('verb') != 'してから'):
                item['modality'] = 'possible'
            result.append(item)
            if match.groupdict().get('verb') == 'してから':
                tail_start = start + match.end()
                tail = text[tail_start:sentence.end()]
                obs = re.fullmatch(r'(.+?)が重くなった。?', tail)
                result.append(draft(str(len(result)+1), text, tail_start, sentence.end(),
                                    obs[1] if obs else '', 'performance.slow' if obs else 'unknown',
                                    '重くなった' if obs else tail, 'observation'))
        else:
            result.append(draft(str(len(result)+1), text, start, sentence.end(), value=quote,
                                content_class='hypothesis' if '原因' in quote else 'observation',
                                modality='possible' if 'かもしれない' in quote else 'asserted'))
    return result


def literal_supported(candidate, intake=None):
    """Reparse evidence, so changing a model's labels cannot authorize a write."""
    from types import SimpleNamespace
    if intake is not None:
        # Exact sentence boundaries matter: a clipped quote must not hide a
        # negation, hypothetical suffix, or correction elsewhere in that sentence.
        expected = extract(intake)
        keys = ('operation_hint', 'content_class', 'subject', 'predicate', 'time',
                'modality', 'polarity', 'basis', 'evidence')
        if not any(all(item[k] == candidate[k] for k in keys)
                   and item['object']['raw'] == candidate['object']['raw'] for item in expected):
            return False
    quote = candidate['evidence']['quote']
    matches = extract(SimpleNamespace(raw_text=quote))
    for item in matches:
        keys = ('operation_hint', 'content_class', 'modality', 'polarity', 'basis')
        if (all(item[k] == candidate[k] for k in keys)
                and item['subject'] == candidate['subject']
                and item['predicate'] == candidate['predicate']
                and item['object']['raw'] == candidate['object']['raw']
                and item['time'] == candidate['time']
                and item['predicate']['concept_hint'] != 'unknown'):
            return True
    # An observation split from a temporal sequence carries only its own quote.
    return (candidate['predicate']['concept_hint'] == 'performance.slow'
            and quote.rstrip('。') == candidate['subject']['mention'] + 'が重くなった'
            and candidate['content_class'] == 'observation'
            and candidate['modality'] == 'asserted'
            and candidate['object']['raw'] == '重くなった'
            and candidate['polarity'] == 'positive')
