# Forge

Forge is a non-commercial, MIT-licensed experiment in reliable AI agent orchestration. It turns a project brief into small, verifiable development tasks and coordinates specialized agents to implement them through a transparent TDD workflow.

## How it works

```text
planner → tester ↔ coder → tester → reviewer
```

- The planner creates small batches of focused tasks.
- The tester and coder iterate using explicit TDD states.
- A fresh, read-only reviewer approves changes or requests corrections.
- A ledger and checkpoints keep progress visible and recoverable.

See [docs/PIPELINE.md](docs/PIPELINE.md) for details.

## Research direction

The project explores:

- reliable autonomous implementation from requirements,
- efficient context management across long-running tasks,
- quality gates and role isolation,
- routing work between different cloud models,
- hybrid cloud and local LLM workflows,
- reducing token cost while preserving implementation quality.

## Quick start

Requirements: Python 3.10+, Git and configured AI agent CLIs.

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m forge.orchestrate \
  --brief game.md \
  --project game \
  --max-tdd-rounds 8
```

Run the tests:

```bash
python3 -m pytest -q
```

## Community

Forge is developed as a private-time AI passion project by Jan Sochiera and Dominik Kuraś. Findings, practical examples and limitations will be shared with Sii Workers through project updates and a recorded presentation.

Feedback and experiments on non-confidential sample projects are welcome.

## License

[MIT](LICENSE)
