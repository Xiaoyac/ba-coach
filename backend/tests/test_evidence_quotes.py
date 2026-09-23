import pytest

from app.evidence_quotes import literal_span
from app.m1_contract import normalize


def test_paragraph_formatting_restores_actual_source():
    source = '开场。情绪与行动相互影响。\n\n先做一点，再观察。结束。'
    assert literal_span('情绪与行动相互影响。先做一点，再观察。', source) == (
        '情绪与行动相互影响。\n\n先做一点，再观察。')


@pytest.mark.parametrize('quote', ['先观察，再做一点。', '先做十分钟。',
                                 '先做一点！再观察。', '   ', None])
def test_word_order_numbers_punctuation_and_empty_quotes_are_not_repaired(quote):
    assert literal_span(quote, '先做一点。\n再观察。') is None


def test_m1_whitespace_repair_keeps_actual_role_and_original_span():
    source = '情绪与行动相互影响。\n\n先做一点，再观察。'
    raw = {'education_quotes': [{'turn': 0, 'quote': source.replace('\n', '')}, None, None, None, None]}
    result = normalize(raw, {}, [('assistant', source)], 'format-case')
    assert result['evidence']['education_0']['quote'] == source
    wrong_role = normalize(raw, {}, [('user', source)], 'format-case')
    assert 'education_0' not in wrong_role['evidence']
