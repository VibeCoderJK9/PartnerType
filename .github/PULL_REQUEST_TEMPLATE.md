## What and why

<!-- What does this change, and which problem does it solve? Link the issue: Fixes #123 -->

## How it was tested

<!-- Tests you added or ran. A bug fix needs a regression test that fails without the fix. -->

## Checklist

- [ ] Tests added or updated, and `./run-tests.sh` passes
- [ ] Permissions are enforced on the server, not only in the UI
- [ ] No user text reaches `innerHTML`; `node scripts/security-sink-audit.mjs` passes
- [ ] New UI text is in the locale files (`locales/en.json`, `locales/pl.json`, `api/locale/pl.json`), not in code
- [ ] Database changes come as an Alembic migration
- [ ] Documentation updated if behaviour visible to users or operators changed
