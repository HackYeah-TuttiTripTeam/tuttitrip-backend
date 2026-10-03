---
name: open-pr
description: Create a correctly named branch, run local checks and open a pull request to develop in tuttitrip-backend with the team's description template. Use when asked to open/create a PR or to ship a change.
---

# Open a PR

1. Branch from fresh `develop`:
   `git fetch origin && git switch -c <type>/<short-kebab-name> origin/develop`
   with `<type>` = `feature`, `fix` or `chore`. The branch name becomes the
   preview URL `https://tuttitrip-api-<slug>.gburek.app`, so keep it short.
2. Run all checks and fix failures (do not suppress lint rules):
   `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
3. Commit with an imperative subject (< 72 chars). No AI attribution trailers.
4. Push and open the PR against `develop` (releases are `develop` -> `main`):

   ```bash
   git push -u origin HEAD
   gh pr create --base develop --title "<subject>" --body-file - <<'MD'
   ## Co i dlaczego
   <1-3 zdania>

   ## Zmiany
   - ...

   ## Jak sprawdzić
   - Preview: https://tuttitrip-api-<slug>.gburek.app/docs
   - ...

   ## Checklist
   - [ ] ruff / ty / pytest zielone lokalnie
   - [ ] migracja dołączona (jeśli zmieniły się modele)
   - [ ] `.env.example` zaktualizowany (jeśli zmieniły się Settings)
   MD
   ```
5. Wait for the `checks` job (and `deploy` for the preview) to go green:
   `gh pr checks --watch`.
