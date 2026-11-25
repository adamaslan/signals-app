"""Offline tests for the actual signal report renderer, no provider execution."""
from scripts.generate_signal_report import _render_report
from types import SimpleNamespace
import unittest



class SignalReportMarkdownTest(unittest.TestCase):
    def render(self, description):
        signal = SimpleNamespace(strength='BULLISH', category='SUPPORT_RESISTANCE',
                                 signal='NEAR SUPPORT', description=description)
        confluence = SimpleNamespace(score=.5, bias='bullish', action='BUY',
                                     bull_count=1, bear_count=0)
        return _render_report('SYNTHETIC', '3mo', '2026-11-24T21:00:00Z', 200,
                               1., [signal], False, confluence)

    def test_multiline_and_pipe_description_stays_in_one_four_column_row(self):
        rendered = self.render('support | resistance\r\nsecond line\rthird line')
        rows = rendered.split('## All Signals (latest bar)\n', 1)[1].strip().splitlines()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1].count('|'), 5)
        self.assertIn('support &#124; resistance second line third line', rows[-1])

    def test_report_renders_html_and_entities_as_literal_text(self):
        rendered = self.render('<img src=x onerror=alert(1)> &amp;')
        self.assertNotIn('<img', rendered)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt; &amp;amp;', rendered)

    def test_existing_plain_text_and_counts_are_unchanged(self):
        rendered = self.render('Close is within 2% of confirmed support')
        self.assertIn('| Total signals | 1 |', rendered)
        self.assertIn('| SUPPORT_RESISTANCE | 1 | 100% |', rendered)
        self.assertIn('| NEAR SUPPORT | BULLISH | SUPPORT_RESISTANCE | Close is within 2% of confirmed support |', rendered)


if __name__ == '__main__':
    unittest.main()
