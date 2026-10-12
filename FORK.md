# jdvmi00/reachy_mini

This fork carries a runtime camera zoom patch for the Reachy Mini daemon
for as long as that patch is not in a Pollen release.

Pollen's `feat/camera-zoom` branch is the four-commit series proposed
upstream. Leave it untouched. The branch this fork ships from is
`reachy-bot/zoom`.

## Install

```bash
pip install "git+https://github.com/jdvmi00/reachy_mini@v1.11.0+zoom.1"
```

Replace the tag with the latest `v<upstream>+zoom.1` tag. The installed
package version is the same string without the leading `v`
(`1.11.0+zoom.1`), so `reachy_mini.__version__` identifies a zoom build.

## Branch and tags

| Name | What it is |
| --- | --- |
| `reachy-bot/zoom` | Latest upstream stable release, plus the zoom commits, plus one version-stamp commit |
| `vX.Y.Z+zoom.1` | Immutable tag of that branch for upstream release `vX.Y.Z` |
| `main` | Upstream `main`, plus this file and `.github/workflows/carry-zoom.yml` |

Stable upstream releases are tags named `vX.Y.Z` (no `rc`, no `.dev`).
Prerelease tags are ignored. Each upstream release gets one zoom tag,
always `+zoom.1`.

The version stamp commit subject is exactly `Stamp zoom package version`.
It sets `version` in `pyproject.toml` and the matching `reachy-mini`
entry in `uv.lock` to `X.Y.Z+zoom.1`. Setuptools already reads
`[project].version`, and `uv lock --check` accepts that local version,
so the build system is unchanged. The stamp is rewritten for each
release; it is not part of the cherry-picked patch.

## Automation

`.github/workflows/carry-zoom.yml` runs every day at 06:17 UTC and on
manual dispatch (Actions → Carry camera zoom → Run workflow).

1. Fetch `pollen-robotics/reachy_mini`. Never push there.
2. Merge `upstream/main` into this fork's `main` when upstream has moved.
   `main` cannot fast-forward, because of the fork commit, so this is a
   merge. This file and the workflow are kept. On a merge conflict,
   nothing is pushed and a GitHub issue is opened.
3. If the latest stable upstream tag already has a `vX.Y.Z+zoom.1` tag,
   stop.
4. Otherwise cherry-pick the non-stamp commits from `reachy-bot/zoom`
   onto that tag, stamp `X.Y.Z+zoom.1`, and run
   `tests/unit_tests/test_camera_zoom.py`, `ruff check`, and `mypy`.
5. On success, push `reachy-bot/zoom` and the new tag. A new upstream
   release is not a fast-forward of the old zoom branch, so that push
   uses `--force-with-lease`. Tags are never force-pushed.
6. On a cherry-pick conflict, an empty patch (zoom already in the
   release), or a test failure, open or comment on an issue and push
   nothing.

Workflow commits and tags use `Jim Martin <jimmartin@gmail.com>`.

A dry run does the same work and then skips every push. Use it from
manual dispatch to confirm a carry before publishing a tag.

## Resolve a conflict by hand

The failed Actions log and the GitHub issue name the upstream tag, the
commit that conflicted, and the files.

```bash
git fetch https://github.com/pollen-robotics/reachy_mini.git --tags
git fetch origin reachy-bot/zoom
git checkout -B reachy-bot/zoom vX.Y.Z
git log --reverse --format='%H %s' vA.B.C..origin/reachy-bot/zoom
```

`vA.B.C` is the release currently under `reachy-bot/zoom`. Cherry-pick
every commit from that log except those whose subject is
`Stamp zoom package version`. Fix the conflicts, then set the package
version:

- `pyproject.toml`: `version = "X.Y.Z+zoom.1"`
- `uv.lock`: the `reachy-mini` package `version` field, same string

Commit that stamp with the subject `Stamp zoom package version` and
nothing else in the commit. Then:

```bash
uv sync --frozen --all-extras --group dev
uv run pytest tests/unit_tests/test_camera_zoom.py
uv run ruff check
uv run mypy
git push --force-with-lease origin reachy-bot/zoom
git push origin vX.Y.Z+zoom.1
```

Close the issue. If you push the repaired branch and not the tag,
re-run the workflow; it will test the branch and publish the tag.

## Retire this fork

Do this when a Pollen release already contains camera zoom. The
workflow also stops itself in that case: a carry that changes no code
files an issue and does not tag.

1. Disable or delete `.github/workflows/carry-zoom.yml` so the schedule
   cannot run again.
2. Point installs at Pollen's release (PyPI `reachy-mini`, or
   `pollen-robotics/reachy_mini` at `vX.Y.Z`).
3. Delete `reachy-bot/zoom` and the `v*+zoom.1` tags when nothing still
   installs from them.
4. To make `main` match upstream exactly, including dropping this file:

```bash
git fetch https://github.com/pollen-robotics/reachy_mini.git main
git push --force-with-lease origin FETCH_HEAD:main
```

That force-push removes the fork commit from `main`. `feat/camera-zoom`
can stay; it is the upstream proposal and is not part of this release line.

## One-time setup

Scheduled workflows do not run in a fork until they are enabled, and
Issues are off on a fork by default.

1. Settings → General → Features → turn Issues on.
2. Actions tab → enable workflows if GitHub asks.
3. Settings → Actions → General → Actions permissions → allow actions.
4. Workflow permissions → "Read and write permissions". The workflow's
   `GITHUB_TOKEN` must be allowed to push `reachy-bot/zoom` and tags
   and to open issues. The workflow file asks for `contents: write` and
   `issues: write`; a repository setting of read-only overrides that.
5. Confirm the scheduled workflow is allowed to run (forks disable
   schedules until this is approved). After a Pollen release you can
   also run the workflow by hand instead of waiting for 06:17 UTC.
