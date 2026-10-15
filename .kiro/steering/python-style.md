---
inclusion: always
---

# Python Code Style

Applies to all Python code written in this project.

## Type hints

- All function and method signatures must include type hints for parameters and return values, including tools decorated with `@tool`.
  ```python
  def order_lookup(order_id: str) -> str:
      ...
  ```
- Use `Optional[...]`, `list[...]`, `dict[...]`, etc. precisely rather than leaving a signature untyped or using `Any` as a default escape hatch.

## Inline comments

- Add short, educational inline comments that explain *why* a piece of AWS, Strands, or AgentCore-specific code is doing what it's doing — this is workshop/demo code, and comments should help a reader learn the pattern, not just restate the line.
- Favor clarity over density: a few well-placed comments on non-obvious steps (auth, region config, tool wiring, memory namespace paths) beat comments on every line.

## General scope

- Keep implementations minimal and focused on demonstrating the specific concept being taught in the current step. Do not add error handling, configuration options, or abstractions beyond what's needed to make the example work and be understandable.
