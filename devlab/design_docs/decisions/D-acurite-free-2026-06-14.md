# D-acurite-free-2026-06-14
**title:** Free open-source AcuRite replacement — RTL-SDR capture → CSV → any cloud → web dashboard
**date:** 2026-06-14
**status:** open
**spawned_tickets:** T-acurite-repo-setup, T-acurite-capture-daemon, T-acurite-web-ui, T-consequence-acurite-free

## Decision narrative
AcuRite is moving to an app-only model with a poor UX. This decision establishes a standalone open-source replacement: an RTL-SDR dongle captures raw 433.92 MHz packets via rtl_433, a Python daemon writes weather.csv to a local folder that the user syncs to any cloud storage (OneDrive, Google Drive, Dropbox, etc.), and a single weather.html file served from that public URL displays current conditions. Zero infrastructure, any cloud account, publishable for others to use.

## Hypothesis
After shipping, AcuRite hardware works without the AcuRite app or cloud — sensor data appears in weather.csv and weather.html displays it from a public cloud URL.

## Measurement Signal
weather.csv receives new rows from actual sensors; weather.html loads from a public URL and shows current conditions; Weather Underground station updates on each reading.

## Goal Link
none: personal project + publishable tool — not tied to a G-xxx goal

## Architecture
- **Capture**: rtl_433 subprocess → parse JSON → filter by sensor whitelist → append CSV row + WU upload
- **Storage**: local filesystem (user's cloud-synced folder) — daemon writes, OS sync handles cloud
- **Display**: weather.html fetches sibling weather.csv (derives URL from window.location)
- **Output format**: `<endpoint>.html` and `<endpoint>.csv` — two files, same location
- **No web server**: HTML + CSV served as static files from any cloud storage public URL
- **Android app**: PWA (inline manifest) + music intent anchors (Pandora/YT Music/Amazon)

## Constraints
- stdlib only in acurite-capture.py (no pip install required)
- No UU dependency — standalone repo, publishable independently
- CORS: user's responsibility to pick cloud storage that serves files with correct headers
