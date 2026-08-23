# Contributing

Isaac Assist spans pure Python, a FastAPI service, an Omniverse extension,
and an optional React client. Keep changes inside the smallest relevant
boundary and state which runtime was actually exercised.

## Setup

```bash
python -m pip install -e ".[test]"
cd web/floor-plan-ui && npm ci
```

Use `.[runtime]` only on hosts intended to run ROS, LiveKit, and the complete
service stack.

## Test tiers

- `l0`: hermetic unit and contract tests; no network or installed simulator.
- `l1`: in-process service tests with mocked external systems.
- `l2`: MCP protocol tests.
- `l3`: live Isaac Sim/Kit RPC tests on a labeled self-hosted runner.

Run the hermetic suite with `python -m pytest`. Run one tier explicitly with
`python -m pytest -m l0`. Never label a test `l0` if it imports `pxr`, contacts
a service, needs a GPU, or reads machine-specific state.

Before submitting a change, run the relevant tests, `git diff --check`, and
`npm test && npm run build` when changing the frontend. Do not describe a
scaffold, mocked result, or skipped integration as a live implementation.

## Source and generated data

`service/`, `exts/`, `web/`, `scripts/`, and lowercase `docs/` are source.
Most of `workspace/` is runtime output; only explicitly unignored knowledge,
templates, and benchmark baselines are versioned. Do not commit credentials,
local databases, rendered media, virtual environments, or build output.
