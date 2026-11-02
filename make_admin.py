"""FoodLoop AI - Phase 4 Admin Setup

IMPORTANT:
The existing users table intentionally allows only:
    provider / receiver

This script does NOT change the users.role column.
Instead it creates a separate admin_users table and grants admin
access to an existing FoodLoop user by email.

Usage:
    python make_admin.py user@example.com
"""

import os
import sqlite3
import sys
from pathlib import Path


def resolve_db_path():
    configured = os.getenv("FOODLOOP_DB_PATH", "").strip()
    if configured:
        return Path(configured)

    try:
        import database
        for attr in ("DB_PATH", "DATABASE_PATH", "DATABASE_FILE", "DB_FILE"):
            value = getattr(database, attr, None)
            if value:
                return Path(value)
    except Exception:
        pass

    return Path("foodloop.db")


def main():
    email = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
    if not email:
        print("Usage: python make_admin.py user@example.com")
        raise SystemExit(1)

    db_path = resolve_db_path()
    if not db_path.exists():
        print(f"Database not found: {db_path.resolve()}")
        raise SystemExit(1)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # Never modify the existing users.role CHECK constraint.
        user = conn.execute(
            "SELECT id, email, role FROM users "
            "WHERE lower(email)=lower(?) LIMIT 1",
            (email,)
        ).fetchone()

        if not user:
            print(f"No FoodLoop user found with email: {email}")
            print("Create the user through FoodLoop registration first, then run this command again.")
            raise SystemExit(1)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        existing = conn.execute(
            "SELECT id FROM admin_users WHERE user_id=? OR lower(email)=lower(?) LIMIT 1",
            (int(user["id"]), email)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE admin_users SET user_id=?, email=? WHERE id=?",
                (int(user["id"]), str(user["email"]).strip().lower(), int(existing["id"]))
            )
            action = "Admin access already existed; record refreshed"
        else:
            conn.execute(
                "INSERT INTO admin_users (user_id, email) VALUES (?, ?)",
                (int(user["id"]), str(user["email"]).strip().lower())
            )
            action = "Admin access granted"

        conn.commit()

    print(f"{action} for {user['email']}.")
    print("The user's existing role remains:", user["role"])
    print("Open: http://127.0.0.1:8000/admin")


if __name__ == "__main__":
    main()
