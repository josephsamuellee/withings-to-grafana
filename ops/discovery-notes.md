# Live dual-profile discovery notes

**Status:** Pending — task 5.4 gate

Run on the Pi after `authorize` for the legacy shared login:

```bash
export FITNESS_SYNC_CONFIG=/mnt/raid1/apps/fitness/config/config.toml
/mnt/raid1/apps/fitness/.venv/bin/fitness-sync discover --person person1
```

Record:

| Field | Value |
|-------|-------|
| Date | |
| Withings userid(s) observed | |
| `dual_attributable` | yes / no |
| Operator decision | proceed single-person / migrate accounts / manual import |
| Notes | |

Do not set `dual_person_sync_enabled = true` or enable `person2` until discovery reports reliable attribution (or a second independent OAuth identity is configured).
