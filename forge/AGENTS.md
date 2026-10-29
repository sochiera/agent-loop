# Forge development rules

- Use English in code, prompts, tests, and documentation.
- Keep the orchestration process simple. The brain owns product decisions; the
  controller owns only execution, retries, persistence, and contract checks.
- Optimize total task cost, not the token count of a single prompt. Preserve
  durable information when losing it would force another expensive agent call.
- Do not add a framework or service when the Python standard library is enough.
- Run commands with `python3`, never `python`.
