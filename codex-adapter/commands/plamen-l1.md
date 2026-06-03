---
description: Launch or resume a Plamen L1 infrastructure audit through the deterministic driver.
argument-hint: [light|core|thorough|resume|--fresh] [project-or-config]
---

# plamen-l1

Arguments: `$ARGUMENTS`

Follow `~/.codex/skills/plamen/SKILL.md` with the L1 wizard reference. New configs must set `pipeline = l1` and `cli_backend = codex`.

Do not manually orchestrate Plamen phases and do not spawn audit agents yourself.
Launch only the shared Python driver:

```
python /home/serge/.codex/plamen/scripts/plamen_driver.py "{CONFIG_PATH}"
```

Fresh restart:

```
python /home/serge/.codex/plamen/scripts/plamen_driver.py --fresh "{CONFIG_PATH}"
```
