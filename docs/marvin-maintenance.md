# Maintaining the Marvin fork

The maintained source is [Ixian/openclaw-openwebui-integration](https://github.com/Ixian/openclaw-openwebui-integration), branch `marvin`. The original upstream is Eliav2/openclaw-openwebui-integration. The first fork base is commit `ec8b8ba51be564b0ca29981fef41eaa4f43d8a68`, matching the original installed Functions.

`main` tracks upstream without homelab changes. Work and releases live on `marvin`. Keep credentials, device identities, chat histories, database backups and complete valve exports outside Git. The local checkout is `/Users/grt/Github/Homelab/openclaw-openwebui-integration`.

## What we maintain

We maintain the bridge's Python source, its generated Pipe and Status Action, tests, release manifest and update tooling. Open WebUI and OpenClaw remain official distributions. The homelab keeps its existing Docker Compose image digest. Updating this bridge changes Functions through Open WebUI's API, not a container image or a file edited inside a container.

The feature's presentation state is derived from Gateway events and read APIs. It does not patch Open WebUI's frontend bundle, OpenClaw's database, or the agent's instructions.

## Make an upstream update

1. Review the weekly **Marvin upstream watch** workflow summary for upstream commits. It reports changes and never merges, publishes, deploys or connects to the homelab.
2. Fetch upstream and create an integration branch from `marvin`. Merge the selected upstream commit into that branch. Resolve conflicts in source fragments, then run `python3 build.py --all`.
3. Run the unit suite, artifact loader checks and fresh-stack browser flow. Review changes to Gateway payloads, Open WebUI rendering, dependencies and the function update API. Generate the Activity screenshot through the Screenshots workflow.
4. Merge the tested integration branch into `marvin`. Package a new versioned release. Update the homelab source lock only after the deployed checks pass.

Example source commands:

```bash
git fetch upstream
git switch marvin
git switch -c integrate/upstream-YYYYMMDD
git merge UPSTREAM_COMMIT
python3 build.py --all
python3 build.py --check-all
```

The upstream remote is `https://github.com/Eliav2/openclaw-openwebui-integration.git`. No automatic merge or image tag follows `latest`.

## Build and release

The **Marvin CI** workflow runs the unit tests on Python 3.12 and the fresh-stack browser tests on the exact Open WebUI slim image digest and OpenClaw 2026.10.9. Its browser and API regression tests exclude the five upstream scenarios that stop or restart a service. The inherited upstream restart tests remain in the repository for separately authorized test runs.

After all checks pass and the working tree is clean:

```bash
python3 scripts/marvin_release.py --out /tmp/marvin-release --ci-run PASSING_RUN_ID
```

The output contains the matched Pipe, Status Action and `release.json`, including the source commit, passing CI run, upstream base, tested versions, image digest and SHA-256 of both Functions. Tag that source commit, publish the files together on the fork's GitHub Releases page, and deploy that exact set. The two Functions must have the same `MARVIN_RELEASE` constant. The Action refuses a mismatched activity connection.

## Deploy and recover

The host wrapper lives in the private Homelab-Austin repository, `scripts/update-marvin-bridge.py`. It loads credentials only on Austin, checks installed checksums against the current deployment lock, verifies required Gateway read APIs, and refuses to update while an Open WebUI session has an active run.

Before the first change it exports both complete Functions, their valves and activation settings into a private backup directory, and takes an online SQLite backup with an integrity check. A second idle check precedes the update. It then updates the two stable Function IDs, restores their exact configuration, verifies installed checksums and Gateway model loading, and records the new source lock. The update is not an atomic two-Function transaction: any failure triggers paired rollback. Do not start chats during this short update window.

Backups stay under the persistent bridge data directory, mode 0700 for the directory and 0600 for exports. They contain secrets. Existing data backups include that directory. They are never GitHub release assets.

A future Open WebUI image upgrade is separate: edit the version and digest in the private Compose repository, test the bridge against that image first, and obtain approval before recreating the container. Rolling back bridge Functions uses the protected paired export. Rolling back the whole application database requires a stopped application and separate service-stop approval.
