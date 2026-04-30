# Contributing

Thanks for your interest. PRs are welcome — bugs, new skills, integration improvements, docs.

## Sanitization rule (non-negotiable)

Nothing personal in the diff. No real names, real email addresses, real Slack workspace IDs, real API tokens, vault file content, recruiter lists, or paths that include your home directory or vault location. Use placeholders (`<your-name>`, `${SECOND_BRAIN_VAULT}`, `alex-chen`) instead.

If you accidentally commit something personal, force-push isn't enough — assume the secret is compromised and rotate it.

## Adding a skill

1. Create `.claude/skills/<your-skill-slug>/SKILL.md`.
2. Frontmatter must include `name` and `description`. The description is what Claude Code uses to decide when to invoke the skill, so be specific about trigger phrases and what the skill does.
3. The body of `SKILL.md` is the instructions Claude reads when the skill is invoked. Keep them concrete: file paths to read, commands to run, exact output format.
4. Vault paths use `${SECOND_BRAIN_VAULT}`, never a hardcoded path.
5. Reference user identity placeholders only — never your own name or anyone real.
6. Tests in `tests/` are encouraged for any logic the skill depends on (parsers, formatters).

## Adding an integration

1. Add the module under `.claude/scripts/integrations/<service>.py`.
2. All credentials load via `_env.py` (or the OAuth flow inside the module). Never hardcode tokens or paths.
3. Register the integration in `.claude/scripts/integrations/registry.py` with an `enabled` flag the user can toggle.
4. Expose a CLI surface via `query.py <service> <command>`.
5. Return dataclasses, not raw API payloads — calling code stays stable across vendor API changes.
6. Document the env vars and required scopes in `.env.example` and the README.

## Testing

```bash
python -m pytest tests/
```

Add tests next to the code they cover. Mock external APIs at the boundary (Gmail SDK, Slack SDK, etc.).

## Code style

- Plain stdlib + the SDKs already pinned. Avoid pulling in heavy frameworks.
- Type hints are nice but not required.
- No comments that just restate what the code does.
- Comments are for *why* something is non-obvious — invariants, workarounds for upstream bugs, security decisions.

## Reporting issues

GitHub Issues is the right place. Include:

- What you ran
- Expected behavior
- Actual behavior
- Python version + OS
- Whether the integration with credentials is involved (if so, redact tokens before pasting logs)
