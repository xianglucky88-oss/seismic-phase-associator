# Seismic Phase Associator

Associate station phase picks into versioned earthquake events.

## Modules

- `src/seismic_phase_associator/models.py`: immutable result contract and
  JSON-safe copying.
- `src/seismic_phase_associator/parser.py`: input validation, record
  extraction, clock-correction validation.
- `src/seismic_phase_associator/engine.py`: domain calculation only
  (deduplication, window association, isolation).
- `src/seismic_phase_associator/report.py`: stable output envelope and
  canonical ordering.
- `src/seismic_phase_associator/pipeline.py`: orchestration boundary.
- `src/seismic_phase_associator/cli.py`: deterministic JSON CLI.

## Semantics (the contract the tests pin down)

- **Clock correction** is looked up by exact station name and applied exactly
  once, only to that station's picks.  Invalid entries are diagnosed and
  treated as zero; there is no fuzzy or cross-station matching.
- **Window** is inclusive: `|corrected_time - origin| <= window`.  The event
  anchor is the earliest qualifying pick, so a cluster cannot drift forward by
  chaining neighbours.
- **Repeated delivery** of the same `(station, phase, time)` is collapsed
  before association.  The representative is chosen canonically (independent
  of arrival order), and every displaced copy is reported as
  `duplicate_pick` with its raw payload.
- **Slots** are `(station, phase)`: P and S from one station may share an
  event, but a second same-phase pick may not.
- **Isolation**: an event needs two distinct stations.  Everything else is
  reported with an explicit reason (`invalid_record`, `missing_station`,
  `missing_phase`, `unsupported_phase`, `missing_time`, `invalid_time`,
  `duplicate_pick`, `insufficient_stations`) and the untouched raw pick.
- **Ordering**: events are sorted by origin/signature and numbered after
  sorting; picks are sorted by corrected time; rejections and diagnostics are
  sorted.  Permuting the input cannot change the report.
- **Conservation**: every input row appears exactly once, either as an accepted
  pick or as one rejection.

## Run

```bash
PYTHONPATH=src python3 -m seismic_phase_associator.cli
PYTHONPATH=src python3 -m seismic_phase_associator.cli --window 0.5
python3 -m unittest discover -s tests -v
```

The fixture is local and deterministic; no network or credentials are required.
The suite uses only the standard library (no pytest/hypothesis needed).

## Verification layers

| File | Purpose |
| --- | --- |
| `tests/oracle.py` | Independent from-scratch reference implementation of the documented semantics, plus a recursive differ that attributes every difference to raw picks. |
| `tests/generators.py` | Seeded payload generators (normal / messy / duplicate) and a 1-minimal shrinking reducer. |
| `tests/support.py` | Comparison views and the differential assertion helper. |
| `tests/test_oracle_modules.py` | Differential tests per module boundary: parser, engine, pipeline, report. |
| `tests/test_timeline_properties.py` | Time-axis properties: translation equivariance, per-station correction as translation, applied-once, corrected-time window, scaling, inclusive boundaries. |
| `tests/test_metamorphic.py` | Permutation invariance, repeated delivery, time-disjoint batching, isolated-station insertion, empty batches. |
| `tests/test_invariants.py` | Conservation, single-counting, isolation reasons, raw-payload fidelity, correction scope. |
| `tests/test_fault_injection.py` | Hostile payloads plus injected parser/engine/report faults; proves the oracle detects each. |
| `tests/test_shrinking.py` | Random search → minimal counterexample for leaked correction, exclusive boundary, missing dedup and a lossy report. |
| `tests/test_mutation.py` | Verifies the mutation harness: control passes, curated mutants are killed. |
| `tests/mutation_runner.py` | 21 single-point mutants (correction scope, window, slots, dedup, station counting, report ordering/counts/raw picks); kills a mutant when the probe disagrees with the oracle. |

```bash
python3 tests/mutation_runner.py          # human-readable kill table
python3 tests/mutation_runner.py --json   # machine-readable
python3 tests/mutation_runner.py --list   # mutant catalogue
```

Failure output always names the raw pick(s) that caused the difference, e.g.

```
root.events[0].origin_time: expected=10.0 actual=10.02
raw_pick=[{'station': 'A', 'phase': 'P', 'time': 10.02, 'magnitude': 2.1}, ...]
```

No earthquake location model is added: association is time-window grouping
plus station/phase bookkeeping only.
