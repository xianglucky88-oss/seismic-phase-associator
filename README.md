# Seismic Phase Associator

Associate station phase picks into versioned earthquake events.

## Modules

- `src/seismic_phase_associator/models.py`: immutable result contract.
- `src/seismic_phase_associator/parser.py`: input validation and record extraction.
- `src/seismic_phase_associator/engine.py`: domain calculation only.
- `src/seismic_phase_associator/report.py`: stable output envelope.
- `src/seismic_phase_associator/pipeline.py`: orchestration boundary.
- `src/seismic_phase_associator/cli.py`: deterministic JSON CLI.

## Run

```bash
PYTHONPATH=src python -m seismic_phase_associator.cli
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture is local and deterministic; no network or credentials are required.

## Data contract and maintenance handover

The repository is a deterministic baseline. Most of the vocabulary used by the
catalogue team (confidence, unassociated reason, stable event identity, magnitude
source, catalogue revisions) is **not implemented in code**. The documentation
below states that explicitly and labels every statement as implemented,
fixture-pinned, or not implemented.

| Document | Purpose |
|---|---|
| `docs/DATA_CONTRACT.md` | Phase and event data contract; pick identity, station correction, confidence, unassociated reason, event identity, magnitude source, field/version change review checklist. |
| `docs/SCENARIOS.md` | Five minimal scenarios (late pick, duplicate, retraction, merge, station-config change) plus the impact matrix, with verified numbers. |
| `docs/CATALOG_REGISTER.md` | Catalogue revision register: version keys, revision tables, responsibility questions, known unregisterable cases. |
| `docs/REVIEW_AND_ROLLBACK.md` | Review levels, executable review steps, rollback procedure and non-rollbackable cases. |

Supporting, non-production tools:

```bash
# Regenerate the scenario table used by the docs
PYTHONPATH=src python3 tools/scenario_report.py

# Differential replay for change-impact review (exit 0=no diff, 1=diff, 2=error)
PYTHONPATH=src python3 tools/replay_diff.py OLD.json NEW.json

# Station-config review against a candidate correction table
PYTHONPATH=src python3 tools/replay_diff.py SAME.json SAME.json --new-corrections clock.json

# Catalogue run_key (input fingerprint; pair it with a code_ref)
python3 tools/run_key.py fixtures/input.json
```

`tools/` adds review tooling only. It does not add or change association logic.
The scenario fixtures live in `fixtures/scenarios/` and are pinned by
`tests/test_scenarios.py`; the tools are pinned by `tests/test_tools.py`.
