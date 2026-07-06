"""AiderDevice — wraps the aider CLI as a subprocess-invoked rack builder.

Lazy/empty by design: importing this package must not eager-import psycopg2 or
the bus (skeleton cold-start rule). Import device.py / shim.py / runner.py
explicitly.

aider is an EXTERNAL DEPENDENCY: installed in its own venv (default
~/.aider-venv), invoked by subprocess only — NEVER imported into
unseen_university/. runner.py is standalone (no bus) so it drives an aider build
on any box (incl. a fresh Windows box) before a rack exists; device.py/shim.py
layer Granny dispatch on top of it.
"""
