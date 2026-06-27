---
name: notify
description: Manage notification settings for this CC session — status/set/pending. Reads and writes ~/.unseen_university/CC-wild-0001/notifications.cfg. Use to inspect or change SILENT/QUIET/LOUD delivery levels without editing config files manually.
model: haiku
---

# /notify — Notification settings CLI

Manage SILENT/QUIET/LOUD delivery config for this CC instance.

## Args
- `/notify status` — show current config and agent busy-state
- `/notify set LOUD|QUIET|SILENT` — set default delivery level
- `/notify set <sender> LOUD|QUIET|SILENT` — add per-sender override
- `/notify pending` — drain and show QUIET queue

## Device home

Config lives at `${CC_DEVICE_HOME:-$HOME/.unseen_university/CC-wild-0001}`.
Set `CC_DEVICE_HOME` to override for a non-default instance.

---

## Steps

### 1. Identify subcommand from args

- First word is `status` → run **status** command
- First word is `set`, second is a valid level (LOUD/QUIET/SILENT) → run **set-default** command with LEVEL=second word
- First word is `set`, second is not a valid level → run **set-override** command with SENDER=second word, LEVEL=third word
- First word is `pending` → run **pending** command

### 2. Run the command

**status**

```bash
python3 -c "
from pathlib import Path; import os
from unseen_university.notify import NotificationConfig
from unseen_university._uu_root import uu_home
device_home = Path(os.environ.get('CC_DEVICE_HOME') or
    uu_home() + '/CC-wild-0001')
cfg = NotificationConfig.load(device_home)
busy = (Path.home() / '.granny/available/CC.0.available.false').exists()
state = 'busy (effective default: SILENT)' if busy else 'idle'
print(f'Default level : {cfg.default_level.value}')
print(f'Agent state   : {state}')
if cfg.overrides:
    print('Per-sender overrides:')
    for sender, level in sorted(cfg.overrides.items()):
        print(f'  {sender}: {level.value}')
else:
    print('Per-sender overrides: none')
print(f'Config at     : {device_home}/notifications.cfg')
"
```

**set-default** — replace `LEVEL` with the arg value (LOUD, QUIET, or SILENT)

```bash
python3 -c "
from pathlib import Path; import os
from unseen_university.notify import NotificationConfig, DeliveryMode
from unseen_university._uu_root import uu_home
device_home = Path(os.environ.get('CC_DEVICE_HOME') or
    uu_home() + '/CC-wild-0001')
cfg = NotificationConfig.load(device_home)
old = cfg.default_level.value
cfg.default_level = DeliveryMode('LEVEL')
cfg.save(device_home)
print(f'Default level: {old} → {cfg.default_level.value}')
"
```

**set-override** — replace `SENDER` with the sender name, `LEVEL` with the level

```bash
python3 -c "
from pathlib import Path; import os
from unseen_university.notify import NotificationConfig, DeliveryMode
from unseen_university._uu_root import uu_home
device_home = Path(os.environ.get('CC_DEVICE_HOME') or
    uu_home() + '/CC-wild-0001')
cfg = NotificationConfig.load(device_home)
old = cfg.overrides.get('SENDER')
old_str = old.value if old else '(none)'
cfg.overrides['SENDER'] = DeliveryMode('LEVEL')
cfg.save(device_home)
print(f'SENDER: {old_str} → {cfg.overrides[\"SENDER\"].value}')
"
```

**pending** — drain QUIET queue (read + clear)

```bash
python3 -c "
from pathlib import Path; import os
from unseen_university.notification_dispatcher import NotificationDispatcher
from unseen_university._uu_root import uu_home
device_home = Path(os.environ.get('CC_DEVICE_HOME') or
    uu_home() + '/CC-wild-0001')
nd = NotificationDispatcher(device_home=device_home)
entries = nd.drain_pending()
if not entries:
    print('nothing pending')
else:
    print(f'{len(entries)} pending:')
    for e in entries:
        print(f'  [{e[\"ts\"]}] {e[\"sender\"]}: {e[\"summary\"]}')
    print('(queue cleared)')
"
```

### 3. Report

Surface the command output directly. No wrapping — the output is the answer.

For `set-default` and `set-override`, confirm with a one-liner: e.g. `Default level: QUIET → LOUD`.
For `pending`, if entries were drained, mention the count and that the queue was cleared.
