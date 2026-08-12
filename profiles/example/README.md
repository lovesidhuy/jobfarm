# profiles/example — starter template

Copy this tree to `profiles/<yourname>/` and edit:

```bash
cp -r profiles/example profiles/<yourname>
$EDITOR profiles/<yourname>/general/profile.yaml
$EDITOR profiles/<yourname>/general/searches.yaml
```

What each file does:

| File | Purpose |
| --- | --- |
| `profile.yaml` | Identity of the profile: `JOB_PROFILE`, bot name, config module, QA answer bank path, resume paths, non-secret env overlays. |
| `searches.yaml` | Where search terms come from + geo policy + which portals/bots discover for this profile. |

Rules that keep the system safe:

1. **Never put secrets in YAML.** Tokens/keys resolve via the secret manager
   layer (Infisical → `.env`). Run `jobbots doctor` to verify presence.
2. **Answers live in the frozen config modules** (`automation_monorepo/config/
   <name>/questions.py`), not in YAML. The manifests reference them.
3. **Every job carries an explicit profile.** `job_profile` here becomes the
   `JOB_PROFILE` env var that selects answers, thresholds, and resume.
4. Verify your setup: `jobbots doctor --quick` then `jobbots qa check`.
