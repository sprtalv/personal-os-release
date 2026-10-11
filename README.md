# Personal OS

An experimental Skill for organizing selected personal evidence, plans, corrections, decisions, and follow-up outcomes. This repository contains generic workflow guidance, Python tools, templates, and synthetic tests. It contains no personal archive.

## Install

1. Make .agents/skills/personal-os/SKILL.md accessible to an assistant host that supports local Skills. Host support and automatic triggering must be verified separately.
2. Use Python 3.10+ on Linux. Create an isolated environment and install .agents/skills/personal-os/scripts/requirements.txt.
3. Create a separate private data directory. Copy .agents/skills/personal-os/assets/settings.example.json to its settings.json; fill only verified values. Keep data and credentials outside this public repository.
4. Inspect commands with python .agents/skills/personal-os/scripts/records.py --help and python .agents/skills/personal-os/scripts/followups.py --help. Run tests with python -B -m unittest discover -s tests -q.

records.py validates and saves structured Markdown records with provenance and cross-references. followups.py tracks due questions and outcomes. publish.py optionally publishes selected files from a separately configured private Git data root. Read the Skill and its references before writes.

## Limits

Scripts enforce structural rules, not semantic accuracy. Search is lexical; interpretation, contradiction review, and advice evaluation need source review. No assistant integration, continuous chat capture, scheduler, message delivery, external task/calendar connection, or backup is installed here. Synthetic tests do not establish long-term effectiveness or better personal advice.

## License

No license has been granted for this repository. MIT is a reasonable permissive option after the copyright holder confirms intended terms and attribution. Public availability alone does not grant reuse rights.
