<div align="center">

# Orbita

**From source material to reviewable project documentation.**

Python · LangGraph · React · Local MVP

[Русский](README.md) · [Documentation index](docs/README.md) · [Setup guide](docs/GETTING_STARTED.md)

</div>

Orbita helps analysts turn tasks, files and connected Jira/Confluence sources into requirements, API contracts, architecture documents and implementation tasks. Its web interface shows the graph, stage documents, approvals and estimated model costs.

## Seven workflows

| Graph | Purpose |
| :--- | :--- |
| `agent` | Requirements → API → data and events → architecture → review |
| `prep` | Jira task intake, gaps, research, work plan and initial documentation; configured Jira is required |
| `drawio` | Read a `.drawio` file and document its components and flows |
| `audit` | Check a document package for traceability, formal defects and contradictions |
| `jira` | Turn analysis into backlog and cards; optionally create issues or prepare manual forms |
| `update` | Propose source-backed replacements, validate them and save a separate revision after approval |
| `nt` | Analyze completed load tests: baseline, deterministic SLA verdict, anomaly ranking and bounded LLM investigation |

Five workflows share a pipeline builder; `update` and `nt` have dedicated graphs using shared Orbita components. Workflows are individually selected, not automatically chained. Inputs and examples are in the [workflow guide](docs/WORKFLOWS.md), in Russian. See [NT setup and scope](docs/NT.md) for the completed-test analysis workflow, including load plateaus, diagnostic completeness and comparable-run checks. Live test control is outside this workflow. The SLA verdict is computed from the server-owned metric map; when explicitly enabled, the model may compose a query for a metric outside that map — the code validates it, the operator approves execution, and the resulting series is evidence only.

## Run the complete local application

Use Python 3.11–3.13 and Node.js 22 to match CI. You need a working model API key. Jira, Confluence, PostgreSQL and LangSmith are optional for this setup.

From the repository root, Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix web ci
if [ ! -f .env ]; then cp .env.example .env; fi
```

Edit the root `.env`: set `LLM_API_KEY` and the correct `LLM_PROVIDER` / `LLM_MODEL`. The template uses `deepseek` / `deepseek-v4-flash`; use a model available to your account. Changing the provider does not automatically change the model. For Anthropic, also install `.[anthropic]` into the virtual environment.

For a first run, set:

```dotenv
PUBLISH_TARGET=file
PUBLISH_DIR=published
CONFLUENCE_PUBLISH=1
CONFLUENCE_PUBLISH_MODE=each
PUBLISH_REQUIRE_APPROVAL=1
PIPELINE_REQUIRE_APPROVAL=0
JIRA_CREATE_ISSUES=0
```

Terminal 1:

```bash
.venv/bin/langgraph dev --host 127.0.0.1 --port 2024 --no-browser --allow-blocking
```

Terminal 2:

```bash
npm --prefix web run dev -- --host 127.0.0.1
```

Open **http://localhost:5173**. Select **Архитектурная аналитика**, choose `partial-refund` and ask it to read the materials, identify contradictions and produce a solution design. Review the documents and approve saving. Files appear under `published/`.

On Windows, use `.venv\Scripts\python.exe`, `.venv\Scripts\langgraph.exe` and `npm.cmd`. Set `$env:PYTHONUTF8="1"` in the backend terminal for Unicode output. Full commands are in [Getting started](docs/GETTING_STARTED.md).

## Inputs, outputs and approvals

Put each task in its own subfolder under `input/`, on the backend machine. The browser selects server-side files; it does not upload them. Text files and `.drawio` are supported; PDF, DOCX and images need conversion.

Generated stage documents are not necessarily published files. Check the publication result and destination. Normal publication and Jira creation require approval by default. The update workflow always requires approval before saving its new revision.

`PUBLISH_TARGET=auto` selects Confluence when required settings are present, otherwise files. It does not fall back to files after a Confluence HTTP failure. The historical `CONFLUENCE_PUBLISH` switch disables all document publication, including files.

## Docker and persistence

The shipped Compose file runs the backend and PostgreSQL; it does not include the frontend or mount your input folder. Follow the [deployment guide](docs/DEPLOYMENT.md) for the complete setup and volume mapping.

`CHECKPOINT_BACKEND=postgres` applies to the custom Python runtime and demo, not to `langgraph dev` storage. Recreating the default backend container does not guarantee preservation of its development-server threads.

## Development

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
npm --prefix web run test:engine
npm --prefix web run build
```

Python tests use fake model and HTTP responses. A passing build is not a live Jira/Confluence integration test. Actual workflows and `run_demo.py` call the configured model and can incur costs.

The separate [costmeter package](packages/costmeter/README.md) tracks cache-aware token usage and estimated cost. Prices are a versioned snapshot or explicit overrides; cache experiments are historical evidence, not guaranteed savings.

The MVP is intended for local operation. Set a random `API_ADMIN_TOKEN` (32–256 ASCII characters) before starting: it is required for the entire API, including LangGraph. Shared access also requires TLS and individual authentication. See [SECURITY.md](SECURITY.md).
