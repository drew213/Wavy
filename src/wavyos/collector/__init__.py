"""The audit collector: reads one Klaviyo account into the snapshot files the check engine scores.

A port of the Node collector Wavy ran for its first audits (reference/klaviyo-audit/), producing the
same JSON shapes so audit/snapshot.py and every check read them unchanged. Artifacts live in the
database (audit_artifacts, audit_orders), not on disk. See docs/AUDIT-PLAN.md section 4.
"""
