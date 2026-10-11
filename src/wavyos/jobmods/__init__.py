"""Job handlers. Importing this package registers every job definition with wavyos.jobs.REGISTRY.

Handlers take their dependencies from ctx.services:
  vault              wavyos.vault.Vault, to open connection secrets
  klaviyo_factory    callable (api_key) -> read-only Klaviyo client (tests pass fakes)
  today              optional date, or callable returning one; defaults to today in the client's timezone
"""
from . import client_onboard, contracts, daily_health, knowledge_staleness, results_due  # noqa: F401  (registration side effects)

IMPLEMENTED = (knowledge_staleness.KEY, results_due.KEY, daily_health.KEY)
CONTRACT_ONLY = tuple(d.key for d in contracts.CONTRACTS)
