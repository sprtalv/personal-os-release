# Skin log

Record the person's own observations and actual use separately from the planned routine. Use the same envelope shape as [training](training.md), with a skin event type of `profile`, `routine`, `decision`, `treatment` or `symptom`. A routine can be proposed or confirmed; the assistant cannot confirm it on the person's behalf.

```sh
python scripts/skin_health.py --root ROOT ingest --input report.json
python scripts/skin_health.py --root ROOT state
python scripts/skin_health.py --root ROOT daily
python scripts/skin_health.py --root ROOT review
```

Keep original dates and source IDs. Use null for missing scores and do not copy photos into the archive. Group observations into the daily and weekly reviews; avoid asking again for facts already reported. Only the user's reported decision changes the recorded routine. Never recommend a medication change from this log. New severe symptoms or trouble breathing should be addressed first, independently of a routine or rating.
