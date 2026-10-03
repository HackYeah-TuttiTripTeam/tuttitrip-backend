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
   PR title: `feat:`, `docs:`, `chore:` or `bugfix:` (not `bug:`, that prefix is
   for issues), optionally with a scope, then a Polish description, e.g.
   `feat(backend): Filtrowanie wyjazdów po dacie`. A release PR `develop` -> `main`
   is titled `release: <opis>`. The `PR format` check enforces this and the
   template sections below; a red check does not block the merge button, so
   fix it before merging.
4. Push and open the PR against `develop` (releases are `develop` -> `main`):

   ```bash
   git push -u origin HEAD
   gh pr create --base develop --title "feat(backend): <opis>" --body-file - <<'MD'
   ## Co i dlaczego
   <1-3 zdania: co zmienia PR i po co>

   ## Powiązane issue
   Closes #<numer>

   ## Lista zmian
   - <zmiana>

   ## Jak przetestować
   1. Preview: https://tuttitrip-api-<slug>.gburek.app/docs
   2. <kroki>

   ## Zrzuty ekranu
   nie dotyczy

   ## Checklista
   - [ ] ruff / ty / pytest zielone lokalnie
   - [ ] testy dla nowej logiki
   - [ ] migracja dołączona (jeśli zmieniły się modele)
   - [ ] `.env.example` zaktualizowany (jeśli zmieniły się Settings)
   - [ ] docs / AGENTS.md zaktualizowane, jeśli trzeba
   - [ ] brak sekretów w kodzie, logach i opisie
   MD
   ```
5. Wait for `checks`, `contracts-check` (and `deploy` for the preview) to go
   green: `gh pr checks --watch`.
6. Merge into `develop` with "Squash and merge" (the PR title becomes the
   commit) and delete the feature branch: `gh pr merge --squash --delete-branch`.
   Merge a release PR `develop` -> `main` with "Create a merge commit"
   (`gh pr merge --merge`) and never pass `--delete-branch` for it.
   Release notes need no extra work: the `Release notes` workflow labels the PR
   `type:*` from its title, adds it to the draft release on merge into
   `develop` and publishes the draft with a tag when the release PR lands on
   `main`. Rules: https://github.com/HackYeah-TuttiTripTeam/.github/blob/main/CONTRIBUTING.md
