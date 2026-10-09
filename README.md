# Wavy OS

The backend for Wavy Studios' Lifecycle Client Operating System: agency standards, a knowledge base per client, and the loop that turns every insight into a decision, every decision into tasks, and every finished task into a measured result. Task-level work is pushed out to Claude, ChatGPT or a person, reviewed by a different model, and traced step by step in the console.

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Client Hub contract: [docs/CLIENT-HUB-INTEGRATION.md](docs/CLIENT-HUB-INTEGRATION.md).

## Try it (fictional demo, no model spend)

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
export WAVYOS_DATABASE_URL="sqlite:///$PWD/var/demo.db" WAVYOS_MASTER_KEY_FILE="$PWD/var/master.key"
.venv/bin/wavyos demo                # prints a one-time console token
.venv/bin/wavyos serve --port 8792   # open http://127.0.0.1:8792/console
```

## Run it for a real client

```bash
.venv/bin/wavyos init-db
.venv/bin/wavyos bootstrap --org-name "Wavy Studios" --org-slug wavy --admin-email you@wavystudios.com --admin-name "You"
.venv/bin/wavyos client create --as you@wavystudios.com --name "Acme Skincare" --slug acme --timezone America/Los_Angeles
.venv/bin/wavyos client allow acme --providers anthropic,codex --budget 10 --as you@wavystudios.com
.venv/bin/wavyos provider add anthropic --as you@wavystudios.com   # hidden prompt; sealed in the vault
.venv/bin/wavyos seed standards --as you@wavystudios.com           # Audit OS SOPs + check library
.venv/bin/wavyos schedule && .venv/bin/wavyos work --loop          # the always-on loop
```

Nothing is sent to a model provider for a client until `client allow` names that provider.

## How work moves

| Step | What happens | Where you see it |
|---|---|---|
| Insight | An audit check, a job or an agent records something with evidence | Flow board, left column |
| Decision | An agent proposes a plan with a measurement; a person approves it | Approvals, then Flow |
| Task | Created from the approved decision with a done-when and a QA checklist | Flow board |
| Execution | The task's packet goes to Claude (or ChatGPT, or a person). Every model call, policy check and tool call is traced | Executions → trace panel |
| QA | A different model checks the work against the checklist; failures go back with notes, up to 3 tries | Executions |
| Approval | Anything that sends, publishes or changes something live stops for a person | Approvals |
| Result | Opens when the task is done; the analyst job measures it when the window closes | Flow board, right column |

Every change is in the hash-chained event log (Events, or `wavyos verify-events --client <slug>`).

## Tests

```bash
.venv/bin/python -m pytest -q
```
