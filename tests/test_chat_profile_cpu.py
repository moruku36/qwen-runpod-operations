"""Chat-only CPU regressions. Never perform model, GPU, provider or network work."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_retry_cpu as shared
from qmc_runpod import features, report, retry, smoke, stages


class ChatProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        self.base = shared.CPU()
        self.base.p = self.p

    def tearDown(self):
        self.tmp.cleanup()

    def packet(self):
        p = self.base.packet()
        p['evaluation_profile'] = 'chat_only'
        p['feature_options'] = {'allow_public_search': False}
        return p

    def scoped_report(self):
        return {'trial_id': 'cpu-chat-schema-123', 'execution_mode': 'cpu_mock',
                'evaluation_profile': 'chat_only', 'checks': report.checks_template(),
                'feature_scope': dict.fromkeys(report.SCOPED_FEATURES, 'not_requested')}

    def test_scoped_report_schema_accepts_roundtrip_and_legacy_baseline(self):
        rep = self.scoped_report()
        path = report.write_report(rep, self.p)
        self.assertEqual(json.loads(path.read_text()), rep)
        self.assertEqual(report.validate({'checks': report.checks_template()}), [])
        rep['evaluation_profile'] = 'baseline'; rep['feature_scope'] = {}
        self.assertEqual(report.validate(rep), [])

    def test_scoped_report_schema_rejects_unknown_keys_and_secret_values(self):
        for name, value in (('unrequested_unknown', 'not_requested'), ('asr', 'password private')):
            rep = self.scoped_report(); rep['feature_scope'][name] = value
            with self.subTest(name=name):
                self.assertTrue(report.validate(rep))

    def test_scoped_report_schema_rejects_unknown_profile_or_incomplete_scope(self):
        rep = self.scoped_report(); rep['evaluation_profile'] = 'full_success'
        self.assertTrue(report.validate(rep))
        rep = self.scoped_report(); del rep['feature_scope']['asr']
        self.assertTrue(report.validate(rep))

    def test_scoped_report_schema_rejects_unrequested_feature_pass(self):
        rep = self.scoped_report(); rep['checks']['asr'] = 'pass'
        self.assertTrue(report.validate(rep))
        rep = self.scoped_report(); rep['feature_scope']['asr'] = 'requested'
        self.assertTrue(report.validate(rep))

    def test_chat_packet_needs_no_audio_rights_or_asset(self):
        self.base.validate(self.packet())

    def test_chat_rejects_search_or_audio_fields(self):
        for options in ({'allow_public_search': True}, {'allow_public_search': False, 'audio': 'unused'}):
            p = self.packet(); p['feature_options'] = options
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.base.validate(p)

    def test_legacy_full_profile_still_requires_audio(self):
        p = self.base.packet(); p['feature_options']['licensed_audio'] = False
        with self.assertRaises(ValueError):
            self.base.validate(p)

    def test_chat_trial_command_keeps_deadlines_without_feature_flags(self):
        p = self.packet(); root = Path(p['root']); root.mkdir()
        state = {'run_id': p['run_id'], 'packet_sha256': retry.hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest(),
                 'completed': list(retry.STAGES[:-1]), 'state': 'running'}
        (root / 'retry-checkpoint.json').write_text(json.dumps(state))
        seen = []
        def command(cmd, **kw):
            seen.append((cmd, kw)); return self.base.fake_stage(cmd, **kw)
        with patch.object(retry, 'validate_packet'):
            self.assertEqual(retry.execute(p, shared.ROOT, clock=self.base.clock, command=command), 0)
        cmd, kw = seen[0]
        self.assertIn('--warm', cmd)
        self.assertEqual(cmd[cmd.index('--evaluation-profile') + 1], 'chat_only')
        for flag in ('--features', '--allow-public-search', '--audio', '--transcript-file', '--hold-min'):
            self.assertNotIn(flag, cmd)
        self.assertIn('--test-deadline', cmd); self.assertIn('--export-deadline', cmd)
        self.assertEqual(kw['seconds'], 4800)
        self.assertEqual(json.loads((root / 'retry-checkpoint.json').read_text())['state'], 'chat_execution_complete_export_stop_pending')

    def test_chat_rejects_feature_execution_before_launch(self):
        r = stages.Runner(self.p / 'ws', mirror=None, run_id='cpu-run-123')
        with self.assertRaises(stages.StageError):
            stages.stage_trial(r, evaluation_profile='chat_only', full_features=True)

    def test_chat_mock_reports_requested_metrics_and_unrequested_features(self):
        r = stages.Runner(self.p / 'ws', mirror=None, run_id='cpu-run-123')
        def generate(*a, **kw):
            return {'first_delta_s': .1, 'total_s': .2, 'stream_deltas': 2,
                    'finish_reason': 'stop', 'max_tokens': kw['max_tokens'], 'error': False}
        with shared.trial_fakes(r, generate=generate), patch.object(features, 'run', side_effect=AssertionError('features forbidden')):
            bundle = stages.stage_trial(r, mock=True, warm=True, evaluation_profile='chat_only')
        rep = self.base.read_bundle_report(bundle)
        self.assertEqual(rep['execution_mode'], 'cpu_mock')
        self.assertEqual(rep['evaluation_profile'], 'chat_only')
        self.assertEqual(rep['metrics']['first_max_tokens'], 64)
        self.assertEqual(len(rep['metrics']['warm_samples']), 10)
        self.assertEqual({row['max_tokens'] for row in rep['metrics']['warm_samples']}, {256})
        for name in ('ctx_32k', 'image_understanding', 'web_search', 'asr', 'history_restore'):
            self.assertEqual(rep['checks'][name], 'skipped')
            self.assertEqual(rep['feature_scope'][name], 'not_requested')


if __name__ == '__main__':
    unittest.main(verbosity=2)
