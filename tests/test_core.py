import json
import unittest
from pathlib import Path
from seismic_phase_associator.pipeline import run
from seismic_phase_associator.parser import load_records
from seismic_phase_associator.engine import calculate

def fixture():
    return json.loads((Path(__file__).parents[1] / "fixtures" / "input.json").read_text())

class TestCore(unittest.TestCase):
    def test_pipeline_and_modules(self):
        payload = fixture()
        rows = load_records(payload)
        self.assertTrue(rows)
        data = calculate(rows, payload)
        self.assertIsInstance(data, dict)
        self.assertTrue(data)
        result = run(payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["project"], 'seismic-phase-associator')

        assert result["events"][0]["pick_count"] == 3
