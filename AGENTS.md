# Repository Agent Rules

Before editing source, tests, deployment scripts, or project documentation, run:

```bash
python scripts/sync-main.py
```

Proceed only when it reports `up-to-date` or `fast-forwarded`. The trunk of this
repository is `codex/mass-army` on the `ljnchn` remote; `main` is a stale fork
point and is not used for development. The command must refuse a dirty tree, a
branch other than the trunk, a local-ahead branch, or a divergence from
`ljnchn/codex/mass-army`; resolve those states explicitly instead of merging
automatically.

The live Arena Hero instance runs on the separately managed server. A local
process or checkout is not evidence of the deployed version. Production updates
must use `scripts/update-systemd.sh`, then verify both the service health and
`/opt/arena-hero-agent/current/source-commit`.

Never print or commit API keys, model credentials, environment-file contents,
player identifiers, or private operational logs.
