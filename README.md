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
