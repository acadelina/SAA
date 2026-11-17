# Shifting Hulls

A budgeted decision agent for a fleet that changes its identity.
AI for Connected Industries · Anda Chiorean, Adelina Acsinte, Mocan Sergiu

A simulated port-state-control office: every simulated day, a local LLM agent chooses which K of N vessels to inspect, using AIS-like and compliance event streams, a gradient-boosted risk scorer and an LLM identity resolver.

## Documents

| Milestone | Document |
|---|---|
| 1 · RFC | [docs/Shifting_Hulls_RFC.pdf](docs/Shifting_Hulls_RFC.pdf) |
| 2 · Architecture and implementation plan | [docs/Milestone_2_Architecture_and_Plan.md](docs/Milestone_2_Architecture_and_Plan.md) |

Diagram sources (Mermaid) are in [docs/diagrams/](docs/diagrams/). To re-render one:

```
npx -y @mermaid-js/mermaid-cli@11.4.2 -i docs/diagrams/architecture.mmd -o docs/diagrams/architecture.png -s 2 -b white
```

## Development
Requires Python 3.11+ and Docker.
```sh
cp .env.example .env
make install          # add PYTHON=python3.11 if python3 is older
make test
make lint
make up               # Redis and PostgreSQL, bound to 127.0.0.1
make db-init          # apply sql/001_init.sql (also runs on first start)
make down
```
Run the consumer with local settings:
```sh
set -a; . ./.env; set +a
.venv/bin/python -m shifting_hulls.consumer
```
