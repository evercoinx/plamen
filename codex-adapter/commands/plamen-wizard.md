---
description: Open the Plamen smart-contract audit wizard in Codex.
argument-hint: [light|core|thorough] [project]
---

# plamen-wizard

Arguments: `$ARGUMENTS`

Follow `~/.codex/skills/plamen/plamen-wizard.md`. Do not ask a model-selection question.

Do not manually orchestrate Plamen phases and do not spawn audit agents yourself.
Launch only the shared Python driver:

```
python /home/serge/.codex/plamen/scripts/plamen_driver.py "{CONFIG_PATH}"
```

Fresh restart:

```
python /home/serge/.codex/plamen/scripts/plamen_driver.py --fresh "{CONFIG_PATH}"
```
