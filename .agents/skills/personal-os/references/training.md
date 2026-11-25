# Training and nutrition

Use `scripts/training.py` with a private data root. The assistant converts a report into a JSON envelope; the script records and calculates, rather than interpreting free text as fact.

```sh
python scripts/training.py --root ROOT prepare --template A --text
python scripts/training.py --root ROOT ingest --input report.json
python scripts/training.py --root ROOT state
python scripts/training.py --root ROOT review
python scripts/training.py --root ROOT nutrition
python scripts/training.py --root ROOT validate
```

An envelope has `source: {ref, message_at}`, `raw_text`, and `event: {type, category, event_at, data}`. A session reports the same stable `session_id` throughout, its status, and exercise items with stable IDs and reported sets. Other event types capture equipment, symptoms, context, body measurements, food, habits, policies and follow-up receipts. Use `corrects` for an error in a previously recorded event, not for a change in a later workout. See the [examples](../../../../examples/README.md) for input shapes. The entry point also exposes `dispatch` for host requests and `delete` for explicitly requested local removal. Copies in Git history or backups are separate.

- Keep a suggested prescription separate from actual work. A skipped or partial session is not a completed template. Leave unknown load, date, and reps in reserve unknown.
- Use the same movement, equipment, unit and scheme for progression. Record bar plates versus total load, per-side versus total, and machine display settings accurately. Do not use a change in equipment or poorer form as progression evidence.
- The [example plan](../assets/training/plan.json) offers upper/lower/upper A/B/C, with an optional squat day S only when explicitly requested and recovery checks pass. It starts without prescribed loads and is not a universal training program. This plan records field work if reported; it does not prescribe jumping automatically.
- Include a short period summary in the regular review. Report only actual nutrition and measurements; a meal suggestion is not intake. See [nutrition](training/nutrition.md) and [training principles](training/knowledge.md).
