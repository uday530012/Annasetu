import sqlite3
import hashlib
import secrets

DATABASE_NAME = "foodloop.db"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000
    ).hex()

    return f"{salt}${password_hash}"


# ---------------------------------
# PASSWORDS TO SET
# ---------------------------------

provider_email = "uday530012@gmail.com"
provider_password = "123456"

receiver_email = "uday530014@gmail.com"
receiver_password = "123456"


# ---------------------------------
# CONNECT DATABASE
# ---------------------------------

conn = sqlite3.connect(DATABASE_NAME)
cursor = conn.cursor()


# ---------------------------------
# UPDATE PROVIDER PASSWORD
# ---------------------------------

provider_hash = hash_password(provider_password)

cursor.execute(
    """
    UPDATE users
    SET password_hash = ?
    WHERE email = ?
    """,
    (provider_hash, provider_email)
)

if cursor.rowcount == 1:
    print("Provider password updated successfully.")
else:
    print("Provider email not found.")


# ---------------------------------
# UPDATE RECEIVER PASSWORD
# ---------------------------------

receiver_hash = hash_password(receiver_password)

cursor.execute(
    """
    UPDATE users
    SET password_hash = ?
    WHERE email = ?
    """,
    (receiver_hash, receiver_email)
)

if cursor.rowcount == 1:
    print("Receiver password updated successfully.")
else:
    print("Receiver email not found.")


# ---------------------------------
# SAVE CHANGES
# ---------------------------------

conn.commit()
conn.close()

print()
print("===================================")
print("PASSWORD RESET COMPLETED")
print("===================================")
print()
print("Provider:")
print("Email:", provider_email)
print("Password:", provider_password)
print()
print("Receiver:")
print("Email:", receiver_email)
print("Password:", receiver_password)
print()