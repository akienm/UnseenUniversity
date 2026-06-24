import os as _os

# Backward-compat shim: promote UU_HOME_DB_URL → UU_HOME_DB_URL for one migration cycle.
# Remove once all .env files and shell configs are updated to use UU_HOME_DB_URL.
if "UU_HOME_DB_URL" not in _os.environ and "UU_HOME_DB_URL" in _os.environ:
    _os.environ["UU_HOME_DB_URL"] = _os.environ["UU_HOME_DB_URL"]
