"""Synthetic evidence chain in a temporary root; no external services."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.agents/skills/personal-os/scripts'))
import records
import followups


class IntegrationTests(unittest.TestCase):
    def test_evidence_save_retry_search_and_natural_result_backfill(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'settings.json').write_text(json.dumps({
                'timezone': 'Asia/Shanghai', 'evening_time': '21:00',
                'followup_default_days': 7,
            }))  # Synthetic timing only; production settings are never touched.

            def record(identifier, kind, author='user', related=None):
                meta = dict(schema_version=1, id=identifier, document_type='record',
                            kind=kind, author=author,
                            basis='ai_inference' if author == 'ai' else 'user_statement',
                            domains=['goals_preferences'], status='active',
                            source={'ref': 'synthetic:' + identifier, 'message_at': None,
                                    'excerpt': '合成阅读实验；非真实个人记录。'},
                            time={'event': '2026-10-01', 'expressed': '2026-10-01',
                                  'target': None, 'precision': 'day', 'note': '合成输入，仅知日期。'},
                            related=related or [])
                if kind == 'hypothesis':
                    meta.update(user_endorsement='accepted', evidence_state='untested')
                return {'path': f'data/records/2026-10/{identifier}.md', 'metadata': meta,
                        'body': '合成阅读实验：预期与结果分别保留，认可不代表证实。',
                        'expected_sha256': None}

            items = [record('reading-decision', 'decision'),
                     record('user-explanation', 'hypothesis', related=['reading-decision']),
                     record('ai-explanation', 'hypothesis', 'ai', ['reading-decision'])]
            items.append({'path': 'data/cases/reading-case.md', 'expected_sha256': None,
                          'metadata': dict(schema_version=1, id='reading-case', document_type='case',
                                           kind='decision', status='open', record_refs=['reading-decision'],
                                           user_hypothesis_refs=['user-explanation'],
                                           ai_hypothesis_refs=['ai-explanation'], outcome_refs=[]),
                          'body': '合成案例：尝试提前阅读，原预期是更易集中；实际结果待观察。'})
            bundle = {'changes': items}
            saved = records.apply_bundle(root, bundle, 'synthetic-seed')
            replay = records.apply_bundle(root, bundle, 'synthetic-seed')
            self.assertTrue(replay['idempotent_replay'])
            self.assertEqual(saved['files'], replay['files'])
            self.assertEqual(records.search(root, 'reading-decision')['count'], 4)
            with patch.object(followups, 'utc_now', return_value=datetime.fromisoformat('2026-10-01T20:00:00+08:00')):
                for name in ['short', 'long']:
                    followups.create(root, name, 'reading-case', '合成阅读实验有什么变化？', 'create-' + name)
            # A spontaneous result arrives before any notification. No delivery is fabricated.
            with patch.object(followups, 'utc_now', return_value=datetime.fromisoformat('2026-10-07T20:00:00+08:00')):
                outcome = record('reading-outcome', 'outcome', related=['reading-decision'])
                outcome['body'] = '合成结果：确实尝试，短期较集中；长期效果未知，不能确认因果。'
                case_path = root / 'data/cases/reading-case.md'
                meta, body = records.read_document(case_path)
                meta['outcome_refs'] = ['reading-outcome']
                update = {'path': 'data/cases/reading-case.md', 'metadata': meta,
                          'body': body + '\n实际短期结果见 reading-outcome；长期继续观察。',
                          'expected_sha256': hashlib.sha256(case_path.read_bytes()).hexdigest()}
                records.apply_bundle(root, {'changes': [outcome, update]}, 'result-and-case')
                result = followups.reply(root, 'short', 'complete', 'natural-result',
                                         outcome_ids=['reading-outcome'], source_ref='synthetic:result',
                                         answered_at='2026-10-07T19:00:00+08:00')
                replay = followups.reply(root, 'short', 'complete', 'natural-result',
                                         outcome_ids=['reading-outcome'], source_ref='synthetic:result',
                                         answered_at='2026-10-07T19:00:00+08:00')
                self.assertEqual(result['files'], replay['files'])
                self.assertTrue(replay['idempotent_replay'])
            short, _ = records.read_document(root / 'data/followups/short.md')
            long, _ = records.read_document(root / 'data/followups/long.md')
            self.assertEqual(short['status'], 'closed')
            self.assertIsNone(short['last_asked_at'])
            self.assertEqual(long['status'], 'active')
            self.assertEqual(records.validate_root(root), [])
            user, _ = records.read_document(root / items[1]['path'])
            ai, _ = records.read_document(root / items[2]['path'])
            self.assertEqual((user['author'], ai['author']), ('user', 'ai'))
            self.assertEqual(ai['evidence_state'], 'untested')
            self.assertEqual([x['id'] for x in followups.due(root, '2026-10-08T21:00:00+08:00')['due']], ['long'])


if __name__ == '__main__':
    unittest.main()
