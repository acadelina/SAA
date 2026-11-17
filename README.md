# Shifting Hulls

A budgeted decision agent for a fleet that changes its identity.
AI for Connected Industries · Anda Chiorean, Adelina Acsinte, Mocan Sergiu

A simulated port-state-control office: every simulated day, a local LLM agent chooses which K of N vessels to inspect, using AIS-like and compliance event streams, a gradient-boosted risk scorer and an LLM identity resolver.

## Documents

| Milestone | Document |
|---|---|
| 1 · RFC | [docs/Shifting_Hulls_RFC.pdf](docs/Shifting_Hulls_RFC.pdf) |
| 2 · Architecture and implementation plan | [docs/Milestone_2_Architecture_and_Plan.md](docs/Milestone_2_Architecture_and_Plan.md) |
| Simulator rules (catch, adaptation, seeds) | [docs/specs/simulation_rules.md](docs/specs/simulation_rules.md) |
| Source of every number in the plan | [docs/evidence_log.md](docs/evidence_log.md) |
| How we work (branches, commits, what never to commit) | [docs/team_workflow.md](docs/team_workflow.md) |

Diagram sources (Mermaid) are in [docs/diagrams/](docs/diagrams/). To re-render one:

```
npx -y @mermaid-js/mermaid-cli@11.4.2 -i docs/diagrams/architecture.mmd -o docs/diagrams/architecture.png -s 2 -b white
```

## Development
Requires Python 3.11+ and Docker.
```sh
cp .env.example .env
make install          # add PYTHON=python3.11 if python3 is older
make test             # unit tests, no Docker needed
make lint             # ruff and mypy
make up               # Redis and PostgreSQL, bound to 127.0.0.1
make db-init          # apply sql/001_init.sql (also runs on first start)
make down
```
Run the consumer with local settings:
```sh
set -a; . ./.env; set +a
.venv/bin/python -m shifting_hulls.consumer
```
The ingestion consumer runs on the host, not as a Compose service. Compose starts only the two stateful services, Redis and PostgreSQL. The consumer is a small synchronous process whose logs are most useful in the terminal, and a host process is easier to restart and inspect while the pipeline is being built. It retries the initial PostgreSQL connection and Redis reads with backoff, uses socket timeouts, and handles SIGTERM by finishing the current record and stopping; unacknowledged messages stay in the Redis pending list for the next start. `CONSUMER_MIN_IDLE_MS` (default 60000) and `CONSUMER_PENDING_WARN_S` (default 300) control stale-entry recovery and the pending-age warning.

Integration tests exercise the real PostgreSQL and Redis through an isolated Docker Compose project. They use a unique project name and free host ports (override with `IT_REDIS_PORT` and `IT_POSTGRES_PORT`), apply `sql/001_init.sql`, and remove their containers and volumes when they finish:
```sh
make test-integration
```
