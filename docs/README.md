# CI workflow

This directory should contain `.github/workflows/ci.yml` to enable
GitHub Actions CI. The workflow file currently lives at
`docs/ci-workflow.yml.txt` because the Personal Access Token used for
the initial push did not have the `Workflows` scope (which GitHub
requires for any commit that touches files under `.github/workflows/`).

## To enable CI

Pick one of the two options below.

### Option A — paste via the GitHub UI (fastest)

1. Open `docs/ci-workflow.yml.txt` in this repo.
2. Copy the full contents to the clipboard.
3. Go to https://github.com/toadsage01/Multilingual-Grievance-Document-Intelligence-Platform/actions/new
4. Click "set up a workflow yourself".
5. Paste the contents into the editor, save as `ci.yml`.
6. Commit directly to `main`.

### Option B — regenerate the PAT with `Workflows` scope

1. Go to https://github.com/settings/personal-access-tokens
2. Edit the PAT used for this repo, enable `Workflows` (read and write).
3. Locally:
   ```bash
   git mv docs/ci-workflow.yml.txt .github/workflows/ci.yml
   git commit -m "ci: move workflow file under .github/workflows/"
   git push origin main
   ```

Either way, CI runs on the next push to `main` and on every PR.
The workflow installs dependencies, brings up a `pgvector/pgvector:pg16`
service container and a Redis service container, runs migrations, and
runs both unit and integration tests.
