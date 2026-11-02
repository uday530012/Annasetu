# ============================================================
# FOODLOOP AI - DATABASE
# ============================================================

import sqlite3
import math
import re
from pathlib import Path
from datetime import datetime


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATABASE_NAME = str(BASE_DIR / "foodloop.db")


# ============================================================
# DICT ROW FACTORY
# ============================================================
# This allows:
#     item["name"]
# and:
#     item.get("name")
#
# throughout main.py.

def dict_factory(cursor, row):
    return {
        column[0]: row[index]
        for index, column in enumerate(cursor.description)
    }


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    connection = sqlite3.connect(
        DATABASE_NAME,
        timeout=30
    )

    connection.row_factory = dict_factory

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


# ============================================================
# COLUMN HELPERS
# ============================================================

def get_columns(connection, table_name):
    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def add_column_if_missing(
    connection,
    table_name,
    column_name,
    column_definition
):
    columns = get_columns(
        connection,
        table_name
    )

    if column_name not in columns:
        connection.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {column_definition}
            """
        )


# ============================================================
# CREATE TABLES
# ============================================================

def create_tables():

    connection = get_connection()

    try:

        # ----------------------------------------------------
        # USERS
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


        # ----------------------------------------------------
        # FOOD
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS food (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                food_name TEXT NOT NULL,

                quantity TEXT NOT NULL,

                food_type TEXT NOT NULL,

                location TEXT DEFAULT '',

                contact TEXT DEFAULT '',

                provider_name TEXT DEFAULT '',

                pickup_location TEXT DEFAULT '',

                latitude REAL,

                longitude REAL,

                status TEXT DEFAULT 'available',

                available_until TEXT,

                provider_id INTEGER,

                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


        # ----------------------------------------------------
        # FOOD REQUESTS
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS food_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                food_id INTEGER NOT NULL,

                receiver_name TEXT NOT NULL,

                receiver_organization TEXT NOT NULL,

                receiver_contact TEXT NOT NULL,

                status TEXT DEFAULT 'pending',

                requested_at TEXT NOT NULL,

                created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                receiver_phone TEXT DEFAULT '',

                receiver_location TEXT DEFAULT '',

                message TEXT DEFAULT '',

                receiver_id INTEGER
            )
            """
        )


        # ----------------------------------------------------
        # NOTIFICATIONS
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                receiver_name TEXT DEFAULT '',

                receiver_id INTEGER,

                message TEXT NOT NULL,

                notification_type TEXT DEFAULT 'general',

                is_read INTEGER DEFAULT 0,

                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


        # ----------------------------------------------------
        # RECEIVER LOCATIONS
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS receiver_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                receiver_name TEXT NOT NULL,

                latitude REAL NOT NULL,

                longitude REAL NOT NULL,

                receiver_id INTEGER,

                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


        # ====================================================
        # MIGRATIONS
        # ====================================================

        # FOOD
        add_column_if_missing(
            connection,
            "food",
            "location",
            "TEXT DEFAULT ''"
        )

        add_column_if_missing(
            connection,
            "food",
            "contact",
            "TEXT DEFAULT ''"
        )

        add_column_if_missing(
            connection,
            "food",
            "provider_name",
            "TEXT DEFAULT ''"
        )

        add_column_if_missing(
            connection,
            "food",
            "pickup_location",
            "TEXT DEFAULT ''"
        )

        add_column_if_missing(
            connection,
            "food",
            "latitude",
            "REAL"
        )

        add_column_if_missing(
            connection,
            "food",
            "longitude",
            "REAL"
        )

        add_column_if_missing(
            connection,
            "food",
            "status",
            "TEXT DEFAULT 'available'"
        )

        add_column_if_missing(
            connection,
            "food",
            "available_until",
            "TEXT"
        )

        add_column_if_missing(
            connection,
            "food",
            "provider_id",
            "INTEGER"
        )


        # FOOD REQUESTS
        add_column_if_missing(
            connection,
            "food_requests",
            "receiver_phone",
            "TEXT DEFAULT ''"
        )

        add_column_if_missing(
            connection,
            "food_requests",
            "receiver_location",
            "TEXT DEFAULT ''"
        )

        add_column_if_missing(
            connection,
            "food_requests",
            "message",
            "TEXT DEFAULT ''"
        )

        add_column_if_missing(
            connection,
            "food_requests",
            "receiver_id",
            "INTEGER"
        )

        add_column_if_missing(
            connection,
            "food_requests",
            "created_at",
            "TEXT"
        )


        # NOTIFICATIONS
        add_column_if_missing(
            connection,
            "notifications",
            "receiver_id",
            "INTEGER"
        )

        add_column_if_missing(
            connection,
            "notifications",
            "notification_type",
            "TEXT DEFAULT 'general'"
        )

        add_column_if_missing(
            connection,
            "notifications",
            "is_read",
            "INTEGER DEFAULT 0"
        )


        # RECEIVER LOCATIONS
        add_column_if_missing(
            connection,
            "receiver_locations",
            "receiver_id",
            "INTEGER"
        )

        add_column_if_missing(
            connection,
            "receiver_locations",
            "updated_at",
            "TEXT"
        )


        # ====================================================
        # INDEXES
        # ====================================================

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_food_status
            ON food(status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_food_provider_id
            ON food(provider_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_food_requests_food_id
            ON food_requests(food_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_food_requests_receiver_id
            ON food_requests(receiver_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_notifications_receiver_id
            ON notifications(receiver_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_receiver_locations_receiver_id
            ON receiver_locations(receiver_id)
            """
        )


        connection.commit()

    finally:
        connection.close()


# ============================================================
# USER FUNCTIONS
# ============================================================

def create_user(
    name,
    email,
    password_hash,
    role
):

    connection = get_connection()

    try:

        cursor = connection.execute(
            """
            INSERT INTO users
            (
                name,
                email,
                password_hash,
                role
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                name,
                email,
                password_hash,
                role
            )
        )

        connection.commit()

        return {
            "success": True,
            "user_id": cursor.lastrowid
        }

    finally:
        connection.close()


def get_user_by_email(email):

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *
            FROM users
            WHERE LOWER(email) = LOWER(?)
            LIMIT 1
            """,
            (email,)
        ).fetchone()

    finally:
        connection.close()


def get_user_by_id(user_id):

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,)
        ).fetchone()

    finally:
        connection.close()


def update_user_password_hash(user_id, password_hash):
    """Update only the password hash for an existing user."""

    if not password_hash:
        raise ValueError("Password hash is required.")

    connection = get_connection()

    try:
        cursor = connection.execute(
            """
            UPDATE users
            SET password_hash = ?
            WHERE id = ?
            """,
            (password_hash, int(user_id))
        )

        connection.commit()
        return cursor.rowcount > 0

    finally:
        connection.close()


# ============================================================
# FOOD FUNCTIONS
# ============================================================

def _validate_food_quantity(quantity):
    """Validate that the food quantity contains a positive numeric amount."""
    import re

    if quantity is None:
        return False

    text = str(quantity).strip()
    if not text:
        return False

    match = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)", text)
    if not match:
        match = re.match(r"^(\d+(?:\.\d+)?)", text)

    if not match:
        return False

    try:
        return float(match.group(1)) > 0
    except (TypeError, ValueError):
        return False


def _food_is_expired(food):
    """Return True when available_until is present and already in the past."""
    from datetime import datetime

    value = food.get("available_until") if food else None
    if not value:
        return False

    try:
        expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
        return expiry <= now
    except (TypeError, ValueError):
        # Keep backward compatibility with older records whose date format
        # cannot be parsed. The existing availability status remains authoritative.
        return False


def add_food(
    food_name,
    quantity,
    food_type,
    location="",
    contact="",
    provider_name="",
    pickup_location="",
    latitude=None,
    longitude=None,
    provider_id=None,
    available_until=None
):

    # Security Step 2.3: reject zero, negative, or malformed quantities.
    if not _validate_food_quantity(quantity):
        return {
            "success": False,
            "message": "Quantity must be greater than zero."
        }

    connection = get_connection()

    try:

        cursor = connection.execute(
            """
            INSERT INTO food
            (
                food_name,
                quantity,
                food_type,
                location,
                contact,
                provider_name,
                pickup_location,
                latitude,
                longitude,
                status,
                available_until,
                provider_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)
            """,
            (
                food_name,
                quantity,
                food_type,
                location,
                contact,
                provider_name,
                pickup_location,
                latitude,
                longitude,
                available_until,
                provider_id
            )
        )

        connection.commit()

        return {
            "success": True,
            "food_id": cursor.lastrowid
        }

    finally:
        connection.close()


def get_food_by_id(food_id):

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *
            FROM food
            WHERE id = ?
            LIMIT 1
            """,
            (food_id,)
        ).fetchone()

    finally:
        connection.close()


def get_available_food():

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *
            FROM food
            WHERE LOWER(COALESCE(status, 'available'))
                  = 'available'
            ORDER BY id DESC
            """
        ).fetchall()

    finally:
        connection.close()


def get_all_food():

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *
            FROM food
            ORDER BY id DESC
            """
        ).fetchall()

    finally:
        connection.close()


# ============================================================
# FOOD OWNERSHIP
# ============================================================

def food_belongs_to_provider(
    food_id,
    provider_id
):

    food = get_food_by_id(food_id)

    if not food:
        return False

    stored_provider_id = food.get(
        "provider_id"
    )

    # New records should use provider_id.
    if stored_provider_id is not None:

        return str(
            stored_provider_id
        ) == str(
            provider_id
        )

    # Compatibility with older food records.
    provider = get_user_by_id(provider_id)

    if not provider:
        return False

    return str(
        food.get("provider_name", "")
    ).strip().lower() == str(
        provider.get("name", "")
    ).strip().lower()


# ============================================================
# FOOD REQUEST FUNCTIONS
# ============================================================

def create_food_request(
    food_id,
    receiver_name=None,
    receiver_contact=None,
    receiver_organization=None,
    receiver_id=None
):

    # --------------------------------------------------------
    # COMPATIBILITY WITH OLDER CALLING STYLE
    # --------------------------------------------------------

    # Older code may use:
    #
    # create_food_request(
    #     food_id,
    #     receiver_name,
    #     receiver_id,
    #     receiver_organization,
    #     receiver_contact
    # )
    #
    # Detect that form.

    if (
        isinstance(receiver_contact, int)
        and isinstance(receiver_organization, str)
        and isinstance(receiver_id, str)
    ):

        old_receiver_id = receiver_contact
        old_organization = receiver_organization
        old_contact = receiver_id

        receiver_contact = old_contact
        receiver_organization = old_organization
        receiver_id = old_receiver_id


    if receiver_name is None:
        receiver_name = ""

    if receiver_contact is None:
        receiver_contact = ""

    if receiver_organization is None:
        receiver_organization = ""

    # --------------------------------------------------------
    # CHECK FOOD
    # --------------------------------------------------------

    food = get_food_by_id(food_id)

    if not food:

        return {
            "success": False,
            "message": "Food not found."
        }


    # --------------------------------------------------------
    # FOOD MUST BE AVAILABLE
    # --------------------------------------------------------

    food_status = str(
        food.get("status", "")
    ).strip().lower()

    if food_status != "available":

        return {
            "success": False,
            "message": "This food is no longer available."
        }

    # Security Step 2.3: an expired listing cannot be requested.
    if _food_is_expired(food):
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE food SET status = 'expired' WHERE id = ? AND LOWER(COALESCE(status, '')) = 'available'",
                (food_id,)
            )
            connection.commit()
        finally:
            connection.close()

        return {
            "success": False,
            "message": "This food is no longer available because its availability time has expired."
        }

    # Security Step 2.3: stored quantity must remain valid.
    if not _validate_food_quantity(food.get("quantity")):
        return {
            "success": False,
            "message": "This food has an invalid quantity and cannot be requested."
        }


    # --------------------------------------------------------
    # DUPLICATE REQUEST PROTECTION
    # --------------------------------------------------------

    if receiver_id is not None:

        existing = get_receiver_food_request(
            food_id,
            receiver_id
        )

    else:

        existing = get_receiver_food_request_by_name(
            food_id,
            receiver_name
        )


    if existing:

        existing_status = str(
            existing.get("status", "")
        ).strip().lower()

        if existing_status not in [
            "rejected",
            "cancelled"
        ]:

            return {
                "success": False,
                "message":
                    "You have already requested this food.",
                "request_id":
                    existing["id"]
            }


    # --------------------------------------------------------
    # CREATE REQUEST
    # --------------------------------------------------------

    now = datetime.now().isoformat(
        timespec="seconds"
    )

    connection = get_connection()

    try:

        cursor = connection.execute(
            """
            INSERT INTO food_requests
            (
                food_id,
                receiver_name,
                receiver_organization,
                receiver_contact,
                status,
                requested_at,
                created_at,
                receiver_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                food_id,
                receiver_name,
                receiver_organization,
                receiver_contact,
                "pending",
                now,
                now,
                receiver_id
            )
        )

        connection.commit()

        return {
            "success": True,
            "request_id": cursor.lastrowid,
            "message":
                "Food request created successfully."
        }

    finally:
        connection.close()


def get_food_requests():

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT
                fr.*,

                f.food_name,
                f.quantity,
                f.food_type,
                f.provider_name,
                f.provider_id,
                f.status AS food_status,
                f.latitude AS food_latitude,
                f.longitude AS food_longitude

            FROM food_requests fr

            LEFT JOIN food f
                ON fr.food_id = f.id

            ORDER BY fr.id DESC
            """
        ).fetchall()

    finally:
        connection.close()


def get_food_request_by_id(
    request_id
):

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT
                fr.*,

                f.food_name,
                f.quantity,
                f.food_type,
                f.provider_name,
                f.provider_id,
                f.status AS food_status,
                f.latitude AS food_latitude,
                f.longitude AS food_longitude

            FROM food_requests fr

            LEFT JOIN food f
                ON fr.food_id = f.id

            WHERE fr.id = ?

            LIMIT 1
            """,
            (request_id,)
        ).fetchone()

    finally:
        connection.close()


def get_receiver_food_request(
    food_id,
    receiver_id
):

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *
            FROM food_requests

            WHERE food_id = ?
            AND receiver_id = ?

            ORDER BY id DESC

            LIMIT 1
            """,
            (
                food_id,
                receiver_id
            )
        ).fetchone()

    finally:
        connection.close()


def get_receiver_food_request_by_name(
    food_id,
    receiver_name
):

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *
            FROM food_requests

            WHERE food_id = ?
            AND LOWER(receiver_name) = LOWER(?)

            ORDER BY id DESC

            LIMIT 1
            """,
            (
                food_id,
                receiver_name
            )
        ).fetchone()

    finally:
        connection.close()


# ============================================================
# REQUEST OWNERSHIP
# ============================================================

def request_belongs_to_receiver(
    request_id,
    receiver_id,
    receiver_name=None
):

    request = get_food_request_by_id(
        request_id
    )

    if not request:
        return False

    stored_receiver_id = request.get(
        "receiver_id"
    )

    if stored_receiver_id is not None:

        return str(
            stored_receiver_id
        ) == str(
            receiver_id
        )

    if receiver_name is None:
        receiver = get_user_by_id(
            receiver_id
        )

        if receiver:
            receiver_name = receiver.get(
                "name"
            )

    return str(
        request.get("receiver_name", "")
    ).strip().lower() == str(
        receiver_name or ""
    ).strip().lower()


def request_belongs_to_provider(
    request_id,
    provider_id
):

    request = get_food_request_by_id(
        request_id
    )

    if not request:
        return False

    food_provider_id = request.get(
        "provider_id"
    )

    if food_provider_id is not None:

        return str(
            food_provider_id
        ) == str(
            provider_id
        )

    # Compatibility for older food rows
    food_id = request.get(
        "food_id"
    )

    return food_belongs_to_provider(
        food_id,
        provider_id
    )


# ============================================================
# REQUEST STATUS
# ============================================================

VALID_REQUEST_STATUSES = {
    "pending",
    "requested",
    "accepted",
    "rejected",
    "collected"
}


VALID_STATUS_TRANSITIONS = {

    "pending": {
        "accepted",
        "rejected"
    },

    "requested": {
        "accepted",
        "rejected"
    },

    "accepted": {
        "collected"
    },

    "rejected": set(),

    "collected": set()
}


def update_food_request_status(
    request_id,
    status
):

    status = str(
        status
    ).strip().lower()

    if status not in VALID_REQUEST_STATUSES:

        return {
            "success": False,
            "message": "Invalid request status."
        }


    request = get_food_request_by_id(
        request_id
    )

    if not request:

        return {
            "success": False,
            "message": "Food request not found."
        }


    current_status = str(
        request.get("status", "pending")
    ).strip().lower()


    # --------------------------------------------------------
    # STATUS TRANSITION PROTECTION
    # --------------------------------------------------------

    # Allow requested/pending compatibility.
    if current_status == "requested":
        allowed = {
            "accepted",
            "rejected"
        }

    else:
        allowed = VALID_STATUS_TRANSITIONS.get(
            current_status,
            set()
        )


    if status != current_status and status not in allowed:

        return {
            "success": False,
            "message":
                f"Invalid status transition: "
                f"{current_status} → {status}."
        }


    connection = get_connection()

    try:

        connection.execute(
            """
            UPDATE food_requests

            SET status = ?

            WHERE id = ?
            """,
            (
                status,
                request_id
            )
        )


        # ----------------------------------------------------
        # WHEN ACCEPTED
        # ----------------------------------------------------

        if status == "accepted":

            connection.execute(
                """
                UPDATE food

                SET status = 'reserved'

                WHERE id = ?
                AND LOWER(COALESCE(status, ''))
                    = 'available'
                """,
                (
                    request["food_id"],
                )
            )


        # ----------------------------------------------------
        # WHEN REJECTED
        # ----------------------------------------------------

        elif status == "rejected":

            # If no other active request exists,
            # make the food available again.

            active_request = connection.execute(
                """
                SELECT id

                FROM food_requests

                WHERE food_id = ?

                AND LOWER(status)
                    IN ('pending', 'requested', 'accepted')

                AND id != ?

                LIMIT 1
                """,
                (
                    request["food_id"],
                    request_id
                )
            ).fetchone()

            if not active_request:

                connection.execute(
                    """
                    UPDATE food

                    SET status = 'available'

                    WHERE id = ?
                    """,
                    (
                        request["food_id"],
                    )
                )


        # ----------------------------------------------------
        # WHEN COLLECTED
        # ----------------------------------------------------

        elif status == "collected":

            connection.execute(
                """
                UPDATE food

                SET status = 'collected'

                WHERE id = ?
                """,
                (
                    request["food_id"],
                )
            )


        connection.commit()

        return {
            "success": True,
            "message":
                f"Request marked as {status}.",
            "request_id": request_id,
            "status": status
        }

    finally:
        connection.close()


# ============================================================
# MARK FOOD COLLECTED
# ============================================================

def mark_food_collected(
    food_id
):

    connection = get_connection()

    try:

        request = connection.execute(
            """
            SELECT *

            FROM food_requests

            WHERE food_id = ?

            AND LOWER(status) = 'accepted'

            ORDER BY id DESC

            LIMIT 1
            """,
            (food_id,)
        ).fetchone()


        if not request:

            # Compatibility:
            # perhaps request already marked collected.

            collected_request = connection.execute(
                """
                SELECT *

                FROM food_requests

                WHERE food_id = ?

                AND LOWER(status) = 'collected'

                ORDER BY id DESC

                LIMIT 1
                """,
                (food_id,)
            ).fetchone()

            if collected_request:

                return {
                    "success": True,
                    "message":
                        "Food is already marked as collected."
                }

            return {
                "success": False,
                "message":
                    "No accepted request found for this food."
            }


        connection.execute(
            """
            UPDATE food_requests

            SET status = 'collected'

            WHERE id = ?
            """,
            (
                request["id"],
            )
        )


        connection.execute(
            """
            UPDATE food

            SET status = 'collected'

            WHERE id = ?
            """,
            (
                food_id,
            )
        )


        connection.commit()

        return {
            "success": True,
            "message":
                "Food marked as collected."
        }

    finally:
        connection.close()


# ============================================================
# SECURITY STEP 2.4 - DATABASE INPUT SANITIZATION
# ============================================================

def _sanitize_db_text(value, max_length=500):
    if value is None:
        return ""
    text = str(value).strip()
    text = "".join(
        ch for ch in text
        if ch in "\t\n\r" or ord(ch) >= 32
    )
    if len(text) > max_length:
        raise ValueError(
            f"Input exceeds the maximum length of {max_length} characters."
        )
    if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", text):
        raise ValueError("Invalid characters in input.")
    if re.search(r"javascript\s*:", text, re.IGNORECASE):
        raise ValueError("Invalid input.")
    return text


# ============================================================
# NOTIFICATIONS
# ============================================================

def create_notification(
    receiver_id=None,
    message="",
    receiver_name=None,
    notification_type="general"
):

    # Compatibility:
    # create_notification("message")

    if (
        isinstance(receiver_id, str)
        and message == ""
    ):

        message = receiver_id
        receiver_id = None


    try:
        message = _sanitize_db_text(message or "", 500)
        notification_type = _sanitize_db_text(
            notification_type or "general",
            50
        )
        receiver_name = _sanitize_db_text(
            receiver_name or "",
            100
        )
    except ValueError as e:
        return {
            "success": False,
            "message": str(e)
        }

    if not message:

        return {
            "success": False,
            "message":
                "Notification message is required."
        }


    # Get receiver name automatically
    if (
        receiver_name is None
        and receiver_id is not None
    ):

        receiver = get_user_by_id(
            receiver_id
        )

        if receiver:
            receiver_name = receiver.get(
                "name"
            )


    connection = get_connection()

    try:

        cursor = connection.execute(
            """
            INSERT INTO notifications
            (
                receiver_name,
                receiver_id,
                message,
                notification_type,
                is_read
            )
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                receiver_name or "",
                receiver_id,
                message,
                notification_type
            )
        )

        connection.commit()

        return {
            "success": True,
            "notification_id":
                cursor.lastrowid
        }

    finally:
        connection.close()


def get_notifications(
    receiver_id=None,
    receiver_name=None
):

    # Compatibility:
    # get_notifications(receiver_name)

    if (
        receiver_name is None
        and isinstance(receiver_id, str)
    ):

        receiver_name = receiver_id
        receiver_id = None


    connection = get_connection()

    try:

        if receiver_id is not None:

            rows = connection.execute(
                """
                SELECT *

                FROM notifications

                WHERE receiver_id = ?

                OR (
                    receiver_id IS NULL
                    AND LOWER(receiver_name)
                        = LOWER(?)
                )

                ORDER BY id DESC
                """,
                (
                    receiver_id,
                    receiver_name or ""
                )
            ).fetchall()

        elif receiver_name:

            rows = connection.execute(
                """
                SELECT *

                FROM notifications

                WHERE LOWER(receiver_name)
                    = LOWER(?)

                ORDER BY id DESC
                """,
                (
                    receiver_name,
                )
            ).fetchall()

        else:

            rows = connection.execute(
                """
                SELECT *

                FROM notifications

                ORDER BY id DESC
                """
            ).fetchall()

        return rows

    finally:
        connection.close()


def get_unread_notification_count(
    receiver_id=None,
    receiver_name=None
):

    if (
        receiver_name is None
        and isinstance(receiver_id, str)
    ):

        receiver_name = receiver_id
        receiver_id = None


    connection = get_connection()

    try:

        if receiver_id is not None:

            row = connection.execute(
                """
                SELECT COUNT(*) AS count

                FROM notifications

                WHERE is_read = 0

                AND (
                    receiver_id = ?

                    OR (
                        receiver_id IS NULL
                        AND LOWER(receiver_name)
                            = LOWER(?)
                    )
                )
                """,
                (
                    receiver_id,
                    receiver_name or ""
                )
            ).fetchone()

        elif receiver_name:

            row = connection.execute(
                """
                SELECT COUNT(*) AS count

                FROM notifications

                WHERE is_read = 0

                AND LOWER(receiver_name)
                    = LOWER(?)
                """,
                (
                    receiver_name,
                )
            ).fetchone()

        else:

            row = connection.execute(
                """
                SELECT COUNT(*) AS count

                FROM notifications

                WHERE is_read = 0
                """
            ).fetchone()

        return int(
            row["count"]
            if row
            else 0
        )

    finally:
        connection.close()


def mark_notification_read(
    notification_id,
    receiver_id=None,
    receiver_name=None
):

    connection = get_connection()

    try:

        notification = connection.execute(
            """
            SELECT *

            FROM notifications

            WHERE id = ?

            LIMIT 1
            """,
            (
                notification_id,
            )
        ).fetchone()


        if not notification:

            return {
                "success": False,
                "message":
                    "Notification not found."
            }


        # ----------------------------------------------------
        # OWNERSHIP CHECK
        # ----------------------------------------------------

        if receiver_id is not None:

            notification_receiver_id = notification.get(
                "receiver_id"
            )

            notification_receiver_name = notification.get(
                "receiver_name",
                ""
            )

            owns_notification = False

            if notification_receiver_id is not None:

                owns_notification = (
                    str(notification_receiver_id)
                    == str(receiver_id)
                )

            else:

                if receiver_name is None:

                    receiver = get_user_by_id(
                        receiver_id
                    )

                    if receiver:
                        receiver_name = receiver.get(
                            "name"
                        )

                owns_notification = (
                    str(
                        notification_receiver_name
                    ).lower()
                    ==
                    str(
                        receiver_name or ""
                    ).lower()
                )

            if not owns_notification:

                return {
                    "success": False,
                    "message":
                        "You are not authorized to modify this notification."
                }


        connection.execute(
            """
            UPDATE notifications

            SET is_read = 1

            WHERE id = ?
            """,
            (
                notification_id,
            )
        )

        connection.commit()

        return {
            "success": True,
            "message":
                "Notification marked as read."
        }

    finally:
        connection.close()


def mark_all_notifications_read(
    receiver_id=None,
    receiver_name=None
):

    # Compatibility
    if (
        receiver_name is None
        and isinstance(receiver_id, str)
    ):

        receiver_name = receiver_id
        receiver_id = None


    connection = get_connection()

    try:

        if receiver_id is not None:

            cursor = connection.execute(
                """
                UPDATE notifications

                SET is_read = 1

                WHERE is_read = 0

                AND (
                    receiver_id = ?

                    OR (
                        receiver_id IS NULL
                        AND LOWER(receiver_name)
                            = LOWER(?)
                    )
                )
                """,
                (
                    receiver_id,
                    receiver_name or ""
                )
            )

        elif receiver_name:

            cursor = connection.execute(
                """
                UPDATE notifications

                SET is_read = 1

                WHERE is_read = 0

                AND LOWER(receiver_name)
                    = LOWER(?)
                """,
                (
                    receiver_name,
                )
            )

        else:

            cursor = connection.execute(
                """
                UPDATE notifications

                SET is_read = 1

                WHERE is_read = 0
                """
            )


        connection.commit()

        return {
            "success": True,
            "message":
                "All notifications marked as read.",
            "updated":
                cursor.rowcount
        }

    finally:
        connection.close()


# ============================================================
# RECEIVER LOCATION
# ============================================================

def save_receiver_location(
    receiver_id=None,
    receiver_name=None,
    latitude=None,
    longitude=None
):

    # --------------------------------------------------------
    # VALIDATE
    # --------------------------------------------------------

    if latitude is None or longitude is None:

        return {
            "success": False,
            "message":
                "Latitude and longitude are required."
        }


    try:

        latitude = float(latitude)
        longitude = float(longitude)

    except Exception:

        return {
            "success": False,
            "message":
                "Invalid latitude or longitude."
        }


    if latitude < -90 or latitude > 90:

        return {
            "success": False,
            "message":
                "Invalid latitude."
        }


    if longitude < -180 or longitude > 180:

        return {
            "success": False,
            "message":
                "Invalid longitude."
        }


    # --------------------------------------------------------
    # COMPATIBILITY
    # --------------------------------------------------------

    if (
        receiver_id is None
        and isinstance(receiver_name, int)
    ):

        receiver_id = receiver_name
        receiver_name = None


    if (
        receiver_name is None
        and receiver_id is not None
    ):

        receiver = get_user_by_id(
            receiver_id
        )

        if receiver:

            receiver_name = receiver.get(
                "name"
            )


    receiver_name = (
        receiver_name
        or ""
    )


    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------
    # Do NOT use ON CONFLICT here.
    #
    # Older databases may not have a UNIQUE constraint
    # on receiver_id.

    connection = get_connection()

    try:

        existing = None

        if receiver_id is not None:

            existing = connection.execute(
                """
                SELECT id

                FROM receiver_locations

                WHERE receiver_id = ?

                ORDER BY id DESC

                LIMIT 1
                """,
                (
                    receiver_id,
                )
            ).fetchone()


        if existing:

            connection.execute(
                """
                UPDATE receiver_locations

                SET
                    receiver_name = ?,
                    latitude = ?,
                    longitude = ?,
                    updated_at = CURRENT_TIMESTAMP

                WHERE id = ?
                """,
                (
                    receiver_name,
                    latitude,
                    longitude,
                    existing["id"]
                )
            )

            location_id = existing["id"]

        else:

            cursor = connection.execute(
                """
                INSERT INTO receiver_locations
                (
                    receiver_name,
                    latitude,
                    longitude,
                    receiver_id,
                    updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    receiver_name,
                    latitude,
                    longitude,
                    receiver_id
                )
            )

            location_id = cursor.lastrowid


        connection.commit()

        return {
            "success": True,
            "location_id": location_id,
            "latitude": latitude,
            "longitude": longitude
        }

    finally:
        connection.close()


def get_receiver_location(
    receiver_id=None,
    receiver_name=None
):

    # Compatibility:
    # get_receiver_location(123)
    # get_receiver_location("Uday")

    if (
        receiver_name is None
        and isinstance(receiver_id, str)
    ):

        # If string is numeric, treat it as ID.
        try:

            receiver_id = int(
                receiver_id
            )

        except Exception:

            receiver_name = receiver_id
            receiver_id = None


    connection = get_connection()

    try:

        if receiver_id is not None:

            row = connection.execute(
                """
                SELECT *

                FROM receiver_locations

                WHERE receiver_id = ?

                ORDER BY id DESC

                LIMIT 1
                """,
                (
                    receiver_id,
                )
            ).fetchone()

            if row:
                return row


        if receiver_name:

            return connection.execute(
                """
                SELECT *

                FROM receiver_locations

                WHERE LOWER(receiver_name)
                    = LOWER(?)

                ORDER BY id DESC

                LIMIT 1
                """,
                (
                    receiver_name,
                )
            ).fetchone()


        return None

    finally:
        connection.close()


def get_all_receiver_locations():

    connection = get_connection()

    try:

        return connection.execute(
            """
            SELECT *

            FROM receiver_locations

            ORDER BY id DESC
            """
        ).fetchall()

    finally:
        connection.close()


# ============================================================
# DISTANCE CALCULATION
# ============================================================

def calculate_distance_km(
    lat1,
    lon1,
    lat2,
    lon2
):

    lat1 = float(lat1)
    lon1 = float(lon1)
    lat2 = float(lat2)
    lon2 = float(lon2)

    earth_radius_km = 6371.0

    lat1_rad = math.radians(
        lat1
    )

    lat2_rad = math.radians(
        lat2
    )

    delta_lat = math.radians(
        lat2 - lat1
    )

    delta_lon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(delta_lat / 2) ** 2
        +
        math.cos(lat1_rad)
        *
        math.cos(lat2_rad)
        *
        math.sin(delta_lon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius_km * c


# ============================================================
# NEARBY RECEIVERS
# ============================================================

def get_nearby_receivers(
    food_latitude,
    food_longitude,
    radius_km=10
):

    try:

        food_latitude = float(
            food_latitude
        )

        food_longitude = float(
            food_longitude
        )

        radius_km = float(
            radius_km
        )

    except Exception:

        return []


    connection = get_connection()

    try:

        locations = connection.execute(
            """
            SELECT *

            FROM receiver_locations
            """
        ).fetchall()

        nearby = []


        for receiver in locations:

            try:

                receiver_lat = float(
                    receiver["latitude"]
                )

                receiver_lon = float(
                    receiver["longitude"]
                )

                distance = calculate_distance_km(
                    food_latitude,
                    food_longitude,
                    receiver_lat,
                    receiver_lon
                )

                if distance <= radius_km:

                    receiver = dict(
                        receiver
                    )

                    receiver["distance_km"] = round(
                        distance,
                        2
                    )

                    nearby.append(
                        receiver
                    )

            except Exception:

                continue


        nearby.sort(
            key=lambda item:
                item.get(
                    "distance_km",
                    999999
                )
        )

        return nearby

    finally:
        connection.close()


# ============================================================
# COMPATIBILITY FUNCTION
# ============================================================

def add_location_columns():

    connection = get_connection()

    try:

        add_column_if_missing(
            connection,
            "food",
            "latitude",
            "REAL"
        )

        add_column_if_missing(
            connection,
            "food",
            "longitude",
            "REAL"
        )

        add_column_if_missing(
            connection,
            "food",
            "pickup_location",
            "TEXT"
        )

        add_column_if_missing(
            connection,
            "receiver_locations",
            "receiver_id",
            "INTEGER"
        )

        add_column_if_missing(
            connection,
            "receiver_locations",
            "updated_at",
            "TEXT"
        )

        connection.commit()

    finally:
        connection.close()


# ============================================================
# INITIALIZE DATABASE
# ============================================================

create_tables()


# ============================================================
# STARTUP MESSAGE
# ============================================================

print(
    "FOODLOOP AI DATABASE READY"
)