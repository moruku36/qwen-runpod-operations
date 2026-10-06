"""Run the accepted C1 contracts and C2 record cases without optional packages."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
MODULES = (
    'test_c1_flow', 'test_execution_boundary', 'test_installed_dispatcher',
    'test_mock_gateway', 'test_mock_sse', 'test_ondemand_controller',
    'test_ondemand_review_regressions', 'test_owui_intent_policy',
    'test_owui_upstream', 'test_private_chat', 'test_private_gateway',
    'test_single_user_host', 'test_single_user_owui',
    'test_c2_controller_records', 'test_owui_planner_regressions',
)

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
