"""Missing bar timestamps must not appear perfectly fresh."""
import pandas as pd
import pytest

from signals_app.indicators.data_quality import score_data_quality


def bars(last):
    index = list(pd.date_range('2026-01-01', periods=199)) + [last]
    return pd.DataFrame({key: [1.0] * 200 for key in
                         ['Open', 'High', 'Low', 'Close', 'Volume']},
                        index=pd.DatetimeIndex(index))


@pytest.mark.parametrize('missing', [pd.NaT, None, float('nan')])
def test_missing_last_timestamp_uses_existing_unparsable_penalty(missing):
    result = score_data_quality(bars(missing), '1y')
    assert result.score == 0.7
    assert len(result.reasons) == 1
    assert result.reasons[0].startswith('unparsable_last_bar_timestamp:')


def test_valid_current_timestamp_still_scores_one():
    result = score_data_quality(bars(pd.Timestamp.now(tz='UTC').tz_localize(None)), '1y')
    assert result.score == 1.0
    assert result.reasons == []
