import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT / '.agents/skills/personal-os/scripts'
EXAMPLES = PROJECT / 'examples'
sys.path.insert(0, str(TOOLS))
import records
import skin_health
import training


class PublicModulesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def example(self, name):
        return json.loads((EXAMPLES / name).read_text())

    def test_training_ingest_correct_and_report(self):
        first = self.example('training-session.json')
        receipt = training.ingest(self.root, first)
        self.assertEqual(receipt['status'], 'local_saved')
        self.assertTrue(training.ingest(self.root, first)['idempotent_replay'])
        edit = self.example('training-session.json')
        edit['source']['ref'] = 'example-workout-correction'
        edit['raw_text'] = 'Correction: it was 42 kg total.'
        edit['corrects'] = receipt['id']
        edit['event']['data']['items'][0]['sets'][0]['load']['value'] = 42
        training.ingest(self.root, edit)
        state = training.state(self.root)
        self.assertEqual(len(state['sessions']), 1)
        self.assertEqual(state['sessions'][0]['items'][0]['sets'][0]['load']['value'], 42)
        for day in ('A', 'B', 'C', 'S'):
            self.assertEqual(training.prepare(self.root, day)['template'], day)
        self.assertEqual(training.weekly(self.root, '2025-01-06', '2025-01-06')['actual_completed'], 0)

    def test_skin_and_training_share_validated_vault(self):
        training.ingest(self.root, self.example('training-session.json'))
        item = self.example('skin-symptom.json')
        skin_health.ingest(self.root, item)
        result = skin_health.review(self.root, start='2025-01-06', end='2025-01-06')
        self.assertEqual(result['symptoms']['stinging']['known_days'], 1)
        self.assertEqual(result['symptoms']['peeling']['known_days'], 0)
        self.assertEqual(records.validate_root(self.root), [])

    def test_cli_quickstart(self):
        def run(script, *args):
            raw = subprocess.check_output([sys.executable, str(TOOLS / script), '--root',
                                           str(self.root), *args], text=True)
            return json.loads(raw)
        self.assertEqual(run('training.py', 'prepare', '--template', 'A')['template'], 'A')
        self.assertEqual(run('training.py', 'ingest', '--input', str(EXAMPLES / 'training-session.json'))['status'], 'local_saved')
        self.assertEqual(run('skin_health.py', 'ingest', '--input', str(EXAMPLES / 'skin-symptom.json'))['status'], 'local_saved')
