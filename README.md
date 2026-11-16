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
