# Running the local tools

Use Python 3.10+ on Linux for the current file-lock implementation. Install scripts/requirements.txt in an isolated environment. Choose a separate private data root; copy assets/settings.example.json there as settings.json and fill only verified values. Null bindings remain unverified until tested.

records.py validate --root ROOT checks document shape and links. records.py search --root ROOT --query QUERY searches keywords. Both may open operations/.writer.lock in the private root. records.py index writes a rebuildable index. The scripts do not check semantic truth.

`records.py impact --root ROOT --record-id ID` lists structured inbound links and exact ID mentions for correction review. A correction save receipt includes `review_required` items; this is a worklist for a person, not automatic prose repair.

To write, read source material and relevant existing records first. Build a JSON bundle with path, complete metadata, body, and expected_sha256 (null for a new file). Run records.py apply --root ROOT --bundle BUNDLE.json --operation-id STABLE_ID. Reuse the ID and identical input only when retrying the same logical change. The tool validates a candidate snapshot, journals, applies files, and reads back local bytes. local_saved does not mean a remote or backup was updated.

followups.py due returns due questions; it does not notify anyone. mark-asked requires a real send reference. reply requires an independently saved outcome record for partial or complete results. Sending, scheduling, chat access, task/calendar integration, and backups need separate host capabilities and authorization.

publish.py is optional for a separately configured private Git data root. Its invocation without --execute is a plan; --execute may commit and push selected private data files. Use it only within authorized scope and verify its remote readback receipt. Never configure this public source repository as a personal-data destination.
