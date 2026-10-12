"""The written half of the audit: section drafts that a model writes from measured facts, an automated
check and critic hold to the ledger and the rubric, and a strategist approves before the brand sees them.

  packet       what a drafter may see for one section (facts, findings, checks, sizing; no personal data)
  prompts      the methodology rules, each section's output shape and the 10-criterion rubric
  facts_check  every number must be in audit_facts; insights without an action are cut
  drafting     draft -> facts check -> cross-provider critic -> revise (max 3 rounds), with a spend cap
  review       the strategist queue: approve, edit (facts-checked, diffed) or reject; republishes the bundle
  writers      Claude and ChatGPT behind one complete() call
"""
