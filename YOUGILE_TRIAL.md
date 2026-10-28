# ARSOLIT — temporary YouGile mode

This experimental branch is intended to **replace**, not supplement, the current Railway service \`ars-smeta-bot\` for a limited test. **Do not merge it to main.** The original bot source and its persistent data remain untouched.

## Exact original Railway configuration (28 October 2026)
- Project: \`agile-nature\`
- Service: \`ars-smeta-bot\` (offline at time of inventory)
- Repository: \`m4rfa20-crypto/arsmailbot\`
- Branch: \`cloud-memory-preview\`
- Original commit SHA: \`270b6fc3f22d17854ce8b6001d3d8982aa0c22f2\`
- Start command: \`python cloud_entry.py\`
- Cron: **none**
- Existing volume: \`/data\` (must not delete, detach, reformat or overwrite)

**CAUTION:** despite the Railway service name, the actual connected repo is \`arsmailbot\`, not \`ars-smeta-bot\`.

## Trial behavior
- Read-only YouGile REST API v2 (GET only), rate-limited to <50 requests/min.
- \`python yougile_trial.py probe\`: validate key and list visible projects.
- \`python yougile_trial.py collect\`: collect boards/columns/tasks **only** for exact project titles in \`YOUGILE_PROJECTS\` (default \`Новь\`). No cross-company personal tasks or \`#Офисные дела\` unless explicitly allowlisted.
- Raw task snapshots (including no-deadline tasks) exported to JSON; only dated tasks go into \`latest.csv\` and the SQLite metric observations.
- Changes in due date, assignee, completion and columns tracked between snapshots.
- Until task logs are validated against live API, snapshot changes do **not** imply an exhaustive original deadline history.

## Configuration
Set \`YOUGILE_API_KEY\` privately through Railway Variables, never in chat or Git.
\`YOUGILE_PROJECTS=Новь\`
\`YOUGILE_OUTPUT_DIR=/data/yougile-trial\`
Optional \`YOUGILE_API_BASE=https://yougile.com/api-v2\`.

No secrets, message contents or task data are committed to GitHub.

## Trial rollout
1. Preserve original configuration above.
2. Connect Railway service to **this trial branch**.
3. Start with command \`python yougile_trial.py probe\`, no cron; verify actual project visibility.
4. Switch to \`python yougile_trial.py collect\`; inspect JSON, CSV and SQLite.
5. Only then consider daily UTC cron \`0 17 * * *\` (20:00 Moscow).
6. The actual Railway service is offline: no live execution can be claimed until billing/deployment is restored.

## Rollback — exact
Stop trial schedule. Restore repository \`m4rfa20-crypto/arsmailbot\`, original branch \`cloud-memory-preview\` or pin SHA \`270b6fc3f22d17854ce8b6001d3d8982aa0c22f2\`; restore \`startCommand=python cloud_entry.py\`, \`cronSchedule=null\`. Keep existing IMAP and Telegram variables and \`/data\` unchanged. Do not delete snapshots until exported or explicitly approved.

## Tests
\`python -m unittest discover -s tests -p 'test_yougile_trial.py' -v\`
