# D-acurite-isolated-network-2026-06-15

**title:** AcuRite hub on isolated network with daemon listener

**date:** 2026-06-15

**status:** open

**spawned_tickets:** T-acurite-usb-isolated-config, T-acurite-isolated-daemon, T-acurite-integrate-weather-html, T-consequence-acurite-isolated-network

## Decision narrative

AcuRite Access 09155M device transmits weather data via HTTP/WU format to myacurite.com. Instead of routing through main network, connect device directly to USB Ethernet port on laptop and isolate it on a dedicated network segment. Set up dnsmasq on the isolated segment to intercept myacurite.com DNS queries and redirect to local IP. Daemon listens on that IP:80, captures device POST requests (WU format), writes to CSV. This is the first ground-up project built from our tools that isn't part of the tool infrastructure itself.

**Architecture:**
- USB Ethernet port: static IP 10.0.0.230, no internet gateway
- Device: DHCP from isolated segment (10.0.0.231)
- dnsmasq: 10.0.0.230:53 resolves *.myacurite.com → 10.0.0.230
- Daemon: listens on 10.0.0.230:80 for device POST, captures WU format, writes weather.csv
- No device reconfiguration needed; DNS redirect handles everything

## Hypothesis

**Observable difference:** Weather data flows in isolated network, is captured and persisted to CSV accessible from dashboard.

**Signal:** CSV file (weather.csv) grows with new sensor readings; weather.html dashboard displays live data.

**Goal link:** G-acurite-demo

## Spawned tickets

- T-acurite-usb-isolated-config (S): Configure USB Ethernet port with static IP 10.0.0.230, DHCP, dnsmasq DNS redirect
- T-acurite-isolated-daemon (S): Daemon on 10.0.0.230:80, parse WU format, merge two-message pattern, write weather.csv
- T-acurite-integrate-weather-html (S): Point weather.html at isolated CSV (symlink or HTTP endpoint)
- T-consequence-acurite-isolated-network (S): Verify data flows, CSV populates, dashboard renders, gate 2026-06-29
