# Reflection and conversation review

If the assistant host provides authorized chat history, read only the bounded conversation range needed for the current question. Preserve message identifiers and timestamps when available; do not claim full history coverage from a partial read. Do not treat an assistant suggestion as a user commitment.

Ask a small number of useful questions about events, feelings, decisions, expectations, and outcomes. Allow the user to skip. Separate a user's explanation from an AI hypothesis. Check relevant prior records and contrary evidence before making a longer-term claim. For an unresolved decision or outcome, link a specific follow-up question. Chat access and proactive scheduling are host capabilities, not provided by this Skill.

## Incremental review
Read only the relevant new portion of the conversation, ending at a fixed message boundary. Match existing follow-ups before asking. Save meaningful updates once using the original source ID. If reading ends at a gap, keep the last contiguous processed position; do not advance past the gap. When a host supports delivery, record an accepted send separately from a reply or confirmed delivery. Use a short, optional check-in.
