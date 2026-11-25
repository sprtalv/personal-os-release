# Try Personal OS with fictional inputs

## In chat: plan, change, result

Give the assistant these four messages, in order:

1. “Demo report is due Wednesday. Survey report is due Thursday.”
2. “The instructor moved the demo report to Friday.”
3. “I submitted the demo report.”
4. “What is still open, and where did you get that?”

Expected: the demo report is due Friday, Wednesday is superseded, and its submission is self-reported. The survey report remains due Thursday, with no reported submission. If a deadline lacks a date or time in the example, do not invent one. Ask the assistant to propose a short plan; only “confirm this plan” or equivalent approves it.

## On the command line: one private test vault

Run from the repository root after installing the dependencies from the README:

```sh
ROOT="$(mktemp -d)"
S=.agents/skills/personal-os/scripts
python "$S/training.py" --root "$ROOT" prepare --template A --text
python "$S/training.py" --root "$ROOT" ingest --input examples/training-session.json
python "$S/skin_health.py" --root "$ROOT" ingest --input examples/skin-symptom.json
python "$S/training.py" --root "$ROOT" review --start 2025-01-06 --end 2025-01-06
python "$S/skin_health.py" --root "$ROOT" review --start 2025-01-06 --end 2025-01-06
python "$S/records.py" validate --root "$ROOT"
```

The output is real local test data, not connected to a fitness or health provider. The example only logs one actual set, not a complete planned session.
