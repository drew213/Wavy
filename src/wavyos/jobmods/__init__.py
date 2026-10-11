"""Job handlers. Importing this package registers every job definition with wavyos.jobs.REGISTRY.

Handlers take their dependencies from ctx.services:
  vault              wavyos.vault.Vault, to open connection secrets
  klaviyo_factory    callable (api_key) -> read-only Klaviyo client (tests pass fakes)
  today              optional date, or callable returning one; defaults to today in the client's timezone
  link_fetcher       optional callable (url) -> {status, final_url, error}; audit.analyze checks template links with it
  writers            optional callable (org_id) -> {provider: narrative Writer}; narrative.draft writes with them
"""
from . import (audit_analyze, audit_collect, client_onboard, contracts, daily_health, knowledge_staleness,  # noqa: F401
               narrative_draft, results_due)  # (registration side effects)

IMPLEMENTED = (knowledge_staleness.KEY, results_due.KEY, daily_health.KEY)
CONTRACT_ONLY = tuple(d.key for d in contracts.CONTRACTS)
