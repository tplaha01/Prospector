done: Strengthen SYSTEM_PROMPT with strict rules (no “whenever possible”)
Add self-improvement loop after scoring (regenerate if score < 75)
Enforce hard filter: do not emit leads with score < 70
Move lead emission to after scoring, not after generation
Reduce max_leads to 1
Tighten search tool prompt to force specific queries (funding stage, role, signals)
Add strict rule in email_gen: first sentence must reference a real signal or skip
Add skip logic if enrichment returns weak/no signals
Add memory feedback event when skipping duplicates
Ensure score_email result is actually used to control flow
Ensure no generic fallback emails are returned
Add iteration cap handling to avoid premature stop
Verify TokenRouter responses are always parsed safely (JSON errors handled)
Ensure frontend only displays qualified leads (score ≥ 70)
Test full flow with 1 ICP and confirm output quality ≥ 75