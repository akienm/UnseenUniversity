"""
unseen_university.migrations — Database migration scripts.

Migrations are idempotent scripts that create or update database schema.
Each migration is a standalone Python module that can be run independently.

Usage:
    python3 unseen_university/migrations/m_<name>.py

Migrations should:
  - Be idempotent (safe to run multiple times)
  - Use CREATE IF NOT EXISTS for tables and indexes
  - Log their progress
  - Verify their work before exiting
"""
