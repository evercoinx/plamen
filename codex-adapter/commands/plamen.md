---
description: Launch or resume a Plamen smart-contract audit through the deterministic driver.
argument-hint: [light|core|thorough|resume|--fresh] [project-or-config]
---

# plamen

Arguments: `$ARGUMENTS`

Follow `~/.codex/skills/plamen/SKILL.md` with the smart-contract wizard reference. New configs must set `cli_backend = codex`.

Do not manually orchestrate Plamen phases and do not spawn audit agents yourself.
Launch only the shared Python driver:

```
python /home/serge/.codex/plamen/scripts/plamen_driver.py "{CONFIG_PATH}"
```

Fresh restart:

```
python /home/serge/.codex/plamen/scripts/plamen_driver.py --fresh "{CONFIG_PATH}"
```
