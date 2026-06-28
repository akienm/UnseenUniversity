# Agent Template

Starter kit for a new UnseenUniversity device. No Igor required.

## Quickstart

```bash
# 1. Copy this folder
cp -r devices/template devices/my_agent

# 2. Rename the classes
sed -i 's/TemplateDevice/MyAgentDevice/g; s/TemplateShim/MyAgentShim/g' \
    devices/my_agent/device.py devices/my_agent/shim.py

# 3. Set your device ID
#    In device.py:  DEVICE_ID = "my_agent"
#    In shim.py:    _device_id = "my_agent"

# 4. Fill in the stubs
#    device.py  — who_am_i(), health(), capabilities(), comms()
#    shim.py    — start(), stop(), self_test()
```

## What each method does

| Method | What to return |
|---|---|
| `who_am_i()` | Name, version, purpose of your agent |
| `requirements()` | pip packages and ports your agent needs |
| `capabilities()` | What message keywords your agent emits/accepts |
| `comms()` | Your comms:// address and direction flags |
| `health()` | Live status — check a port, a process, a DB connection |
| `uptime()` | Seconds since start |
| `startup_errors()` | Errors from the most recent start attempt |
| `logs()` | Paths to your agent's log files |
| `where_and_how()` | Host, PID, launch command |
| `restart()` | Trigger a graceful restart (delegate to shim) |
| `block(reason)` | Prevent rack from restarting (record the reason) |
| `halt()` | Immediate stop |
| `recovery()` | Attempt recovery from degraded state |

## Shim responsibility

The shim owns your agent's lifecycle: start, stop, restart, rollback.
The device provides information. The shim does the work.

Common shim patterns:
- **subprocess**: launch a worker process, store the handle, check `.poll()`
- **thread**: spin up a background thread, store a stop event
- **noop**: if your "device" is stateless (e.g. a query adapter), shim does nothing

## Register with the rack

```python
from skeleton.registry import DeviceRegistry
from config.device_config import DeviceConfig
from devices.my_agent.device import MyAgentDevice
from devices.my_agent.shim import MyAgentShim

shim = MyAgentShim()
device = MyAgentDevice(shim=shim)
shim.start()

registry = DeviceRegistry()
registry.register(
    device_id=device.DEVICE_ID,
    config=DeviceConfig(),
    mailbox=device.comms()["address"],
    name=device.who_am_i()["name"],
)
```
