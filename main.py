# ============================================================
# FOODLOOP AI - MAIN FASTAPI APPLICATION
# ============================================================

import os
import json
import hashlib
import secrets
import inspect
import re
import math
import time
import threading
import sqlite3
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    Request,
    Form,
    Query
)

from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    JSONResponse,
    Response
)

from fastapi.templating import Jinja2Templates

from starlette.middleware.sessions import SessionMiddleware


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# IMPORT DATABASE
# ============================================================

import database


# ============================================================
# IMPORT AI AGENT
# ============================================================

try:
    from ai_agent import (
        run_food_matching_agent,
        run_receiver_selection_agent,
    )
    AI_AGENT_AVAILABLE = True
except Exception as e:
    print("AI AGENT IMPORT ERROR:", e)
    run_food_matching_agent = None
    run_receiver_selection_agent = None
    AI_AGENT_AVAILABLE = False


# ============================================================
# PHASE 3.4 - NOTIFICATION AGENT
# ============================================================

try:
    from notification_agent import run_notification_agent
    NOTIFICATION_AGENT_AVAILABLE = True
except Exception as e:
    print("NOTIFICATION AGENT IMPORT ERROR:", e)
    run_notification_agent = None
    NOTIFICATION_AGENT_AVAILABLE = False


# ============================================================
# PHASE 3.5 - EXPIRY / RESCUE AGENT
# ============================================================

try:
    from expiry_rescue_agent import (
        analyze_food_expiry,
        scan_provider_foods,
    )
    EXPIRY_RESCUE_AGENT_AVAILABLE = True
except Exception as e:
    print("EXPIRY / RESCUE AGENT IMPORT ERROR:", e)
    analyze_food_expiry = None
    scan_provider_foods = None
    EXPIRY_RESCUE_AGENT_AVAILABLE = False


# ============================================================
# PHASE 3.6 - MULTI-AGENT WORKFLOW
# ============================================================

try:
    from workflow_agent import (
        run_multi_agent_workflow,
        run_provider_workflow_scan,
    )
    MULTI_AGENT_WORKFLOW_AVAILABLE = True
except Exception as e:
    print("MULTI-AGENT WORKFLOW IMPORT ERROR:", e)
    run_multi_agent_workflow = None
    run_provider_workflow_scan = None
    MULTI_AGENT_WORKFLOW_AVAILABLE = False


# ============================================================
# PHASE 3.7 - AGENT DASHBOARD
# ============================================================

try:
    from agent_dashboard import get_agent_dashboard
    AGENT_DASHBOARD_AVAILABLE = True
except Exception as e:
    print("AGENT DASHBOARD IMPORT ERROR:", e)
    get_agent_dashboard = None
    AGENT_DASHBOARD_AVAILABLE = False


# ============================================================
# APP CONFIGURATION
# ============================================================

app = FastAPI(
    title="FoodLoop AI",
    description="AI-powered food redistribution platform",
    version="1.0.0"
)


# ============================================================
# SESSION SECRET
# ============================================================

# SECURITY STEP 2.5 - SESSION SECURITY
# Put a long random value in .env as SESSION_SECRET for production.
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)
    print("WARNING: SESSION_SECRET is not set. A temporary session secret was generated.")
    print("Set SESSION_SECRET in .env so sessions survive application restarts.")

try:
    SESSION_MAX_AGE = int(
        os.getenv("SESSION_MAX_AGE", str(60 * 60 * 24 * 7))
    )
except ValueError:
    SESSION_MAX_AGE = 60 * 60 * 24 * 7

if SESSION_MAX_AGE < 300:
    SESSION_MAX_AGE = 300

SESSION_HTTPS_ONLY = os.getenv(
    "SESSION_HTTPS_ONLY",
    "false"
).strip().lower() in {"1", "true", "yes", "on"}

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
    path="/"
)


# ============================================================
# SECURITY STEP 2.6 - CSRF / ABUSE / SECURITY HEADERS
# ============================================================

# Browser-origin validation protects state-changing requests against
# cross-site form/fetch attacks while preserving existing API clients
# that do not send Origin/Referer headers.
CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Basic in-memory rate limiting. This is intentionally lightweight for the
# current single-process deployment; production should use Redis or another
# shared store when running multiple workers/instances.
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_RULES = {
    "/login": (10, 60),
    "/register": (10, 60),
    "/post-food": (20, 60),
    "/api/request-food": (20, 60),
    "/request-food": (20, 60),
    "/food-request/": (30, 60),
    "/food/": (30, 60),
    "/receiver-location": (60, 60),
    "/notifications/": (60, 60),
    "/api/ai-receiver-matches": (30, 60),
    "/api/ai-notify-receivers": (20, 60),
    "/api/ai-expiry-rescue": (20, 60),
    "/api/ai-expiry-rescue-scan": (10, 60),
    "/api/ai-multi-agent-workflow": (10, 60),
    "/api/ai-multi-agent-workflow-scan": (5, 60),
}

_rate_limit_lock = threading.Lock()
_rate_limit_buckets = {}


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def _rate_limit_rule(path: str):
    if path in RATE_LIMIT_RULES:
        return RATE_LIMIT_RULES[path]

    for prefix, rule in RATE_LIMIT_RULES.items():
        if prefix.endswith("/") and path.startswith(prefix):
            return rule

    return None


def _is_rate_limited(request: Request) -> bool:
    rule = _rate_limit_rule(request.url.path)
    if not rule:
        return False

    limit, window = rule
    key = (_client_ip(request), request.method, request.url.path)
    now = time.time()

    with _rate_limit_lock:
        timestamps = _rate_limit_buckets.get(key, [])
        cutoff = now - window
        timestamps = [ts for ts in timestamps if ts > cutoff]

        if len(timestamps) >= limit:
            _rate_limit_buckets[key] = timestamps
            return True

        timestamps.append(now)
        _rate_limit_buckets[key] = timestamps

        # Keep the in-memory structure bounded.
        if len(_rate_limit_buckets) > 5000:
            stale_cutoff = now - window
            stale_keys = [
                bucket_key
                for bucket_key, bucket_values in _rate_limit_buckets.items()
                if not bucket_values or bucket_values[-1] <= stale_cutoff
            ]
            for stale_key in stale_keys[:1000]:
                _rate_limit_buckets.pop(stale_key, None)

    return False


def _same_origin_request(request: Request) -> bool:
    """Validate Origin/Referer when browsers provide them.

    Missing Origin/Referer is allowed for backward compatibility with
    non-browser API clients. A supplied cross-origin value is rejected.
    """
    host = request.headers.get("host", "").split(":")[0].lower()
    if not host:
        return False

    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            return origin_host == host
        except Exception:
            return False

    referer = request.headers.get("referer")
    if referer:
        try:
            parsed = urlparse(referer)
            referer_host = (parsed.hostname or "").lower()
            return referer_host == host
        except Exception:
            return False

    return True


@app.middleware("http")
async def security_step_2_6_middleware(request: Request, call_next):
    # Reject obvious cross-site state-changing browser requests.
    if request.method in CSRF_PROTECTED_METHODS:
        if not _same_origin_request(request):
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "message": "Cross-site request blocked."
                }
            )

    # Basic abuse/rate protection on authentication and sensitive actions.
    if _is_rate_limited(request):
        response = JSONResponse(
            status_code=429,
            content={
                "success": False,
                "message": "Too many requests. Please try again later."
            }
        )
        response.headers["Retry-After"] = str(RATE_LIMIT_WINDOW)
        return response

    response = await call_next(request)

    # Browser security headers. CSP is intentionally not forced here because
    # the existing templates may load inline scripts/styles.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self)"
    response.headers["Cache-Control"] = (
        "no-store" if request.url.path in {
            "/login",
            "/register",
            "/logout",
            "/api/me"
        } else response.headers.get("Cache-Control", "no-cache")
    )

    return response


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR = BASE_DIR / "static"


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

try:
    database.create_tables()
except Exception as e:
    print("DATABASE INITIALIZATION ERROR:", e)


# ============================================================
# TEMPLATE HELPER
# ============================================================

def find_template(candidates):

    for template_name in candidates:

        template_path = TEMPLATES_DIR / template_name

        if template_path.exists():
            return template_name

    return None


def render_page(
    request: Request,
    candidates,
    context=None
):

    if context is None:
        context = {}

    template_name = find_template(candidates)

    if template_name is None:

        return HTMLResponse(
            content=(
                "Template not found. "
                "Expected one of: "
                + ", ".join(candidates)
            ),
            status_code=500
        )

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context
    )


# ============================================================
# PASSWORD HASHING - SECURITY STEP 2.5
# ============================================================

PASSWORD_ITERATIONS = 310000


def hash_password(password: str) -> str:
    """Create a versioned PBKDF2-SHA256 password hash."""

    salt = secrets.token_hex(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS
    ).hex()

    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${password_hash}"


def verify_password(
    password: str,
    stored_password: str
) -> bool:
    """Verify new hashes and legacy FoodLoop hashes."""

    try:
        parts = str(stored_password).split("$")

        if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
            _, iterations_text, salt, stored_hash = parts
            iterations = int(iterations_text)
        elif len(parts) == 2:
            # Backward compatibility with Step 2.4 and older users.
            salt, stored_hash = parts
            iterations = 100000
        else:
            return False

        if iterations <= 0 or iterations > 1000000:
            return False

        password_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations
        ).hex()

        return secrets.compare_digest(
            password_hash,
            stored_hash
        )

    except Exception:
        return False


def password_needs_rehash(stored_password: str) -> bool:
    """Identify legacy or weaker hashes for automatic upgrade on login."""

    try:
        parts = str(stored_password).split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return True
        return int(parts[1]) < PASSWORD_ITERATIONS
    except Exception:
        return True


# ============================================================
# LOGGED-IN USER
# ============================================================

def get_logged_in_user(request: Request):

    user_id = request.session.get("user_id")

    if not user_id:
        return None

    try:

        user = database.get_user_by_id(
            int(user_id)
        )

        return user

    except Exception as e:

        print(
            "GET LOGGED USER ERROR:",
            e
        )

        return None


# ============================================================
# REQUIRE LOGIN
# ============================================================

def require_login(request: Request):

    user = get_logged_in_user(request)

    if not user:
        return None

    return user


# ============================================================
# ROLE CHECK
# ============================================================

def user_has_role(
    user,
    role
):

    if not user:
        return False

    return str(
        user["role"]
    ).lower() == role.lower()


# ============================================================
# SECURITY STEP 2.4 - INPUT VALIDATION / SANITIZATION
# ============================================================

def sanitize_text(value, max_length=200):
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


def validate_email(email):
    email = sanitize_text(email, 254).lower()
    if not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
        email
    ):
        raise ValueError("Invalid email address.")
    return email


def validate_phone(phone):
    phone = sanitize_text(phone, 30)
    digits = re.sub(r"\D", "", phone)
    if not 7 <= len(digits) <= 15:
        raise ValueError("Invalid contact number.")
    if not re.fullmatch(r"[+0-9()\s.-]+", phone):
        raise ValueError("Invalid contact number.")
    return phone


def validate_coordinates(latitude, longitude):
    if latitude is None and longitude is None:
        return None, None
    if latitude is None or longitude is None:
        raise ValueError("Both latitude and longitude are required.")
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        raise ValueError("Invalid latitude or longitude.")
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("Invalid latitude or longitude.")
    if not -90 <= latitude <= 90:
        raise ValueError("Invalid latitude.")
    if not -180 <= longitude <= 180:
        raise ValueError("Invalid longitude.")
    return latitude, longitude


def parse_positive_id(value, field_name="ID"):
    """Validate IDs before using them in database lookups."""
    try:
        text = str(value).strip()
        if not re.fullmatch(r"[1-9]\d*", text):
            raise ValueError
        return int(text)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {field_name}.")


# ============================================================
# ROOT
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def home(
    request: Request
):

    user = get_logged_in_user(request)

    if user:

        if user_has_role(
            user,
            "provider"
        ):

            return RedirectResponse(
                "/provider-dashboard",
                status_code=303
            )

        if user_has_role(
            user,
            "receiver"
        ):

            return RedirectResponse(
                "/receiver-dashboard",
                status_code=303
            )

    return render_page(
        request,
        [
            "index.html",
            "home.html"
        ]
    )


# ============================================================
# REGISTER PAGE
# ============================================================

@app.get(
    "/register",
    response_class=HTMLResponse
)
async def register_page(
    request: Request
):

    return render_page(
        request,
        [
            "register.html",
            "registration.html"
        ]
    )


# ============================================================
# REGISTER USER
# ============================================================

@app.post("/register")
async def register_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):

    try:
        name = sanitize_text(name, 100)
        email = validate_email(email)
        role = sanitize_text(role, 20).lower()
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)

    if not name:
        return HTMLResponse(
            "Name is required.",
            status_code=400
        )

    if not email:
        return HTMLResponse(
            "Email is required.",
            status_code=400
        )

    if len(password) < 6:

        return HTMLResponse(
            "Password must contain at least 6 characters.",
            status_code=400
        )

    if len(password) > 256:

        return HTMLResponse(
            "Password is too long.",
            status_code=400
        )

    if role not in [
        "provider",
        "receiver"
    ]:

        return HTMLResponse(
            "Invalid role.",
            status_code=400
        )


    # --------------------------------------------------------
    # CHECK EXISTING USER
    # --------------------------------------------------------

    existing_user = database.get_user_by_email(
        email
    )

    if existing_user:

        return HTMLResponse(
            "An account with this email already exists.",
            status_code=400
        )


    # --------------------------------------------------------
    # HASH PASSWORD
    # --------------------------------------------------------

    password_hash = hash_password(
        password
    )


    # --------------------------------------------------------
    # CREATE USER
    # --------------------------------------------------------

    try:

        result = database.create_user(
            name,
            email,
            password_hash,
            role
        )

    except TypeError:

        # Compatibility with alternate database signature

        try:

            result = database.create_user(
                name=name,
                email=email,
                password_hash=password_hash,
                role=role
            )

        except Exception as e:

            print(
                "CREATE USER ERROR:",
                e
            )

            return HTMLResponse(
                "Unable to create account.",
                status_code=500
            )

    except Exception as e:

        print(
            "CREATE USER ERROR:",
            e
        )

        return HTMLResponse(
            "Unable to create account.",
            status_code=500
        )


    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    return RedirectResponse(
        "/login",
        status_code=303
    )


# ============================================================
# LOGIN PAGE
# ============================================================

@app.get(
    "/login",
    response_class=HTMLResponse
)
async def login_page(
    request: Request
):

    return render_page(
        request,
        [
            "login.html"
        ]
    )


# ============================================================
# LOGIN USER
# ============================================================

@app.post("/login")
async def login_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):

    try:
        email = validate_email(email)
    except ValueError:
        return HTMLResponse(
            "Invalid email or password.",
            status_code=401
        )

    if len(password) > 256:
        return HTMLResponse(
            "Invalid email or password.",
            status_code=401
        )


    # --------------------------------------------------------
    # FIND USER
    # --------------------------------------------------------

    user = database.get_user_by_email(
        email
    )


    if not user:

        return HTMLResponse(
            "Invalid email or password.",
            status_code=401
        )


    # --------------------------------------------------------
    # VERIFY PASSWORD
    # --------------------------------------------------------

    if not verify_password(
        password,
        user["password_hash"]
    ):

        return HTMLResponse(
            "Invalid email or password.",
            status_code=401
        )


    # --------------------------------------------------------
    # UPGRADE LEGACY/WEAKER PASSWORD HASH
    # --------------------------------------------------------

    if password_needs_rehash(user["password_hash"]):
        try:
            database.update_user_password_hash(
                user["id"],
                hash_password(password)
            )
        except Exception as e:
            # A valid login should not fail just because the optional
            # password-rehash update could not be written.
            print("PASSWORD REHASH ERROR:", e)


    # --------------------------------------------------------
    # CREATE SESSION
    # --------------------------------------------------------

    request.session.clear()
    request.session["session_version"] = 1
    request.session["login_at"] = int(time.time())

    request.session["user_id"] = user["id"]

    request.session["role"] = user["role"]

    request.session["name"] = user["name"]

    request.session["email"] = user["email"]


    # --------------------------------------------------------
    # REDIRECT
    # --------------------------------------------------------

    if user_has_role(
        user,
        "provider"
    ):

        return RedirectResponse(
            "/provider-dashboard",
            status_code=303
        )


    if user_has_role(
        user,
        "receiver"
    ):

        return RedirectResponse(
            "/receiver-dashboard",
            status_code=303
        )


    return RedirectResponse(
        "/",
        status_code=303
    )


# ============================================================
# LOGOUT
# ============================================================

@app.get("/logout")
async def logout(
    request: Request
):

    request.session.clear()

    response = RedirectResponse(
        "/login",
        status_code=303
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# ============================================================
# PROVIDER DASHBOARD
# ============================================================

@app.get(
    "/provider-dashboard",
    response_class=HTMLResponse
)
async def provider_dashboard(
    request: Request
):

    user = require_login(request)

    if not user:

        return RedirectResponse(
            "/login",
            status_code=303
        )


    if not user_has_role(
        user,
        "provider"
    ):

        return RedirectResponse(
            "/receiver-dashboard",
            status_code=303
        )


    # --------------------------------------------------------
    # FOOD
    # --------------------------------------------------------

    try:

        food = database.get_available_food()

        # SECURITY: provider dashboard must only expose food
        # posted by the currently logged-in provider.
        filtered_food = []

        for item in food:

            item = dict(item)

            provider_id = item.get("provider_id")

            if provider_id is not None and str(provider_id) == str(user["id"]):
                filtered_food.append(item)

        food = filtered_food

    except Exception:

        food = []


    # --------------------------------------------------------
    # REQUESTS
    # --------------------------------------------------------

    try:

        all_requests = database.get_food_requests()

        # SECURITY: provider can only see requests for food
        # owned by that provider.
        food_requests = []

        for item in all_requests:

            item = dict(item)

            provider_id = item.get("provider_id")

            if provider_id is not None and str(provider_id) == str(user["id"]):
                food_requests.append(item)

    except Exception:

        food_requests = []


    # --------------------------------------------------------
    # NOTIFICATIONS
    # --------------------------------------------------------

    try:

        notifications = database.get_notifications(
            user["id"]
        )

    except Exception:

        notifications = []


    try:

        unread_count = database.get_unread_notification_count(
            user["id"]
        )

    except Exception:

        unread_count = 0


    return render_page(

        request,

        [
            "provider.html",
            "provider_dashboard.html"
        ],

        {
            "user": user,
            "food": food,
            "food_requests": food_requests,
            "requests": food_requests,
            "notifications": notifications,
            "unread_count": unread_count
        }

    )


# ============================================================
# PROVIDER URL COMPATIBILITY
# ============================================================

@app.get("/provider")
async def provider_alias(request: Request):

    return RedirectResponse(
        "/provider-dashboard",
        status_code=303
    )


# ============================================================
# RECEIVER DASHBOARD
# ============================================================

@app.get(
    "/receiver-dashboard",
    response_class=HTMLResponse
)
async def receiver_dashboard(
    request: Request
):

    user = require_login(request)

    if not user:

        return RedirectResponse(
            "/login",
            status_code=303
        )


    if not user_has_role(
        user,
        "receiver"
    ):

        return RedirectResponse(
            "/provider-dashboard",
            status_code=303
        )


    # --------------------------------------------------------
    # AVAILABLE FOOD
    # --------------------------------------------------------

    try:

        food = database.get_available_food()

    except Exception:

        food = []


    # --------------------------------------------------------
    # REQUESTS
    # --------------------------------------------------------

    try:

        all_requests = database.get_food_requests()

    except Exception:

        all_requests = []


    # --------------------------------------------------------
    # FILTER RECEIVER REQUESTS
    # --------------------------------------------------------

    receiver_requests = []

    for item in all_requests:

        item = dict(item)

        receiver_id = item.get(
            "receiver_id"
        )

        receiver_name = item.get(
            "receiver_name"
        )

        if receiver_id is not None:

            if str(receiver_id) == str(
                user["id"]
            ):

                receiver_requests.append(
                    item
                )

        elif receiver_name:

            if receiver_name.lower() == str(
                user["name"]
            ).lower():

                receiver_requests.append(
                    item
                )


    # --------------------------------------------------------
    # NOTIFICATIONS
    # --------------------------------------------------------

    try:

        notifications = database.get_notifications(
            user["id"]
        )

    except Exception:

        notifications = []


    try:

        unread_count = database.get_unread_notification_count(
            user["id"]
        )

    except Exception:

        unread_count = 0


    # --------------------------------------------------------
    # RECEIVER LOCATION
    # --------------------------------------------------------

    try:

        receiver_location = database.get_receiver_location(
            user["id"]
        )

    except Exception:

        receiver_location = None


    return render_page(

        request,

        [
            "receiver.html",
            "receiver_dashboard.html"
        ],

        {
            "user": user,
            "food": food,
            "food_requests": receiver_requests,
            "requests": receiver_requests,
            "notifications": notifications,
            "unread_count": unread_count,
            "receiver_location": receiver_location
        }

    )


# ============================================================
# RECEIVER URL COMPATIBILITY
# ============================================================

@app.get("/receiver")
async def receiver_alias(request: Request):

    return RedirectResponse(
        "/receiver-dashboard",
        status_code=303
    )


# ============================================================
# ADD FOOD PAGE
# ============================================================

@app.get(
    "/add-food",
    response_class=HTMLResponse
)
async def add_food_page(
    request: Request
):

    user = require_login(request)

    if not user:

        return RedirectResponse(
            "/login",
            status_code=303
        )


    if not user_has_role(
        user,
        "provider"
    ):

        return RedirectResponse(
            "/receiver-dashboard",
            status_code=303
        )


    return render_page(
        request,
        [
            "add_food.html",
            "add-food.html"
        ],
        {
            "user": user
        }
    )


# ============================================================
# ADD FOOD - FORM
# ============================================================

@app.post("/post-food")
async def post_food(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    if not user_has_role(
        user,
        "provider"
    ):

        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only providers can post food."
            }
        )


    # --------------------------------------------------------
    # READ FORM OR JSON
    # --------------------------------------------------------

    try:

        content_type = request.headers.get(
            "content-type",
            ""
        )

        if "application/json" in content_type:

            data = await request.json()

        else:

            form = await request.form()

            data = dict(form)

    except Exception as e:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid request data."
            }
        )


    # --------------------------------------------------------
    # GET VALUES
    # --------------------------------------------------------

    try:
        food_name = sanitize_text(data.get("food_name", ""), 100)
        quantity = sanitize_text(data.get("quantity", ""), 50)
        food_type = sanitize_text(data.get("food_type", ""), 50)
        available_until = sanitize_text(
            data.get("available_until", ""),
            40
        )
        provider_name = sanitize_text(
            data.get("provider_name", user["name"]),
            100
        )
        location = sanitize_text(data.get("location", ""), 250)
        contact = validate_phone(data.get("contact", ""))
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": str(e)
            }
        )


    latitude = data.get(
        "latitude"
    )

    longitude = data.get(
        "longitude"
    )


    # --------------------------------------------------------
    # CONVERT GPS
    # --------------------------------------------------------

    try:

        latitude = (
            float(latitude)
            if latitude not in [
                None,
                "",
                "null"
            ]
            else None
        )

    except Exception:

        latitude = None


    try:

        longitude = (
            float(longitude)
            if longitude not in [
                None,
                "",
                "null"
            ]
            else None
        )

    except Exception:

        longitude = None


    try:
        latitude, longitude = validate_coordinates(
            latitude,
            longitude
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": str(e)
            }
        )


    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not food_name:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Food name is required."
            }
        )


    if not quantity:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Quantity is required."
            }
        )


    # SECURITY STEP 2.3 - positive quantity validation
    # Supports values such as "10", "10 plates", or "5 kg".
    try:
        import re
        quantity_match = re.search(
            r"[-+]?\d+(?:\.\d+)?",
            quantity
        )

        if not quantity_match:
            raise ValueError

        numeric_quantity = float(
            quantity_match.group(0)
        )

        if numeric_quantity <= 0:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "Quantity must be greater than zero."
                }
            )

    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Quantity must contain a valid positive number."
            }
        )


    if not food_type:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Food type is required."
            }
        )


    if not location:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Location is required."
            }
        )


    if not contact:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Contact is required."
            }
        )


    # --------------------------------------------------------
    # ADD FOOD
    # --------------------------------------------------------

    try:

        result = database.add_food(

            food_name=food_name,
            quantity=quantity,
            food_type=food_type,
            location=location,
            contact=contact,
            provider_name=provider_name,
            latitude=latitude,
            longitude=longitude,
            provider_id=user["id"],
            available_until=available_until

        )

    except TypeError:

        # ----------------------------------------------------
        # COMPATIBILITY WITH OLDER DATABASE
        # ----------------------------------------------------

        try:

            result = database.add_food(

                food_name,

                quantity,

                food_type,

                available_until,

                provider_name,

                location,

                contact

            )

        except Exception as e:

            print(
                "ADD FOOD ERROR:",
                e
            )

            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "message": str(e)
                }
            )

    except Exception as e:

        print(
            "ADD FOOD ERROR:",
            e
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


    # --------------------------------------------------------
    # NORMALIZE RESULT
    # --------------------------------------------------------

    if isinstance(
        result,
        dict
    ):

        response_data = result

    else:

        response_data = {
            "success": True,
            "food_id": result,
            "message": "Food posted successfully!"
        }


    # --------------------------------------------------------
    # LOCATION NOTIFICATION
    # --------------------------------------------------------

    if response_data.get(
        "success"
    ):

        food_id = response_data.get(
            "food_id"
        )


        if food_id:

            try:

                nearby_receivers = (
                    database.get_nearby_receivers(
                        latitude,
                        longitude,
                        10
                    )
                    if latitude is not None
                    and longitude is not None
                    else []
                )


                for receiver in nearby_receivers:

                    receiver_id = receiver.get(
                        "receiver_id",
                        receiver.get("id")
                    )


                    if receiver_id:

                        database.create_notification(

                            receiver_id,

                            f"New food available nearby: {food_name}"

                        )

            except Exception as e:

                print(
                    "FOOD NOTIFICATION ERROR:",
                    e
                )


    return JSONResponse(
        content=response_data
    )


# ============================================================
# AVAILABLE FOOD PAGE
# ============================================================

@app.get(
    "/available-foods",
    response_class=HTMLResponse
)
async def available_foods_page(
    request: Request
):

    user = require_login(request)

    if not user:

        return RedirectResponse(
            "/login",
            status_code=303
        )


    try:

        food = database.get_available_food()

    except Exception:

        food = []


    return render_page(

        request,

        [
            "available_foods.html",
            "available-foods.html",
            "find_food.html"
        ],

        {
            "user": user,
            "food": food
        }

    )


# ============================================================
# AVAILABLE FOOD JSON
# ============================================================

@app.get("/available-foods-data")
async def available_foods_data():

    try:

        food = database.get_available_food()

        return {
            "success": True,
            "food": food
        }

    except Exception as e:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


# ============================================================
# API FOOD
# ============================================================

@app.get("/api/food")
async def api_food():

    try:

        food = database.get_available_food()

        return {
            "success": True,
            "food": food
        }

    except Exception as e:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


# ============================================================
# SINGLE FOOD API
# ============================================================

@app.get("/api/food/{food_id}")
async def api_single_food(
    food_id: int
):

    try:

        food = database.get_food_by_id(
            food_id
        )

        if not food:

            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "Food not found."
                }
            )


        return {
            "success": True,
            "food": food
        }

    except Exception as e:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


# ============================================================
# CREATE FOOD REQUEST
# ============================================================

async def process_food_request(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    if not user_has_role(
        user,
        "receiver"
    ):

        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only receivers can request food."
            }
        )


    # --------------------------------------------------------
    # READ JSON / FORM
    # --------------------------------------------------------

    try:

        content_type = request.headers.get(
            "content-type",
            ""
        )

        if "application/json" in content_type:

            data = await request.json()

        else:

            form = await request.form()

            data = dict(form)

    except Exception:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid request data."
            }
        )


    # --------------------------------------------------------
    # VALUES
    # --------------------------------------------------------

    try:

        food_id = int(
            data.get("food_id")
        )

    except Exception:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid food ID."
            }
        )


    try:
        receiver_name = sanitize_text(
            data.get("receiver_name", user["name"]),
            100
        )
        receiver_contact = validate_phone(
            data.get("receiver_contact", "")
        )
        receiver_organization = sanitize_text(
            data.get(
                "receiver_organization",
                data.get(
                    "organization",
                    user.get("organization", "FoodLoop Receiver")
                )
            ),
            150
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": str(e)
            }
        )


    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not receiver_name:

        receiver_name = user["name"]


    if not receiver_contact:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Contact number is required."
            }
        )


    # --------------------------------------------------------
    # CREATE REQUEST
    # --------------------------------------------------------

    try:

        result = database.create_food_request(

            food_id=food_id,

            receiver_name=receiver_name,

            receiver_contact=receiver_contact,

            receiver_organization=receiver_organization,

            receiver_id=user["id"]

        )

    except TypeError:

        # Compatibility with newer database versions

        try:

            result = database.create_food_request(

                food_id=food_id,

                receiver_name=receiver_name,

                receiver_contact=receiver_contact,

                receiver_organization=receiver_organization,

                receiver_id=user["id"]

            )

        except Exception as e:

            print(
                "CREATE REQUEST ERROR:",
                e
            )

            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "message": str(e)
                }
            )

    except Exception as e:

        print(
            "CREATE REQUEST ERROR:",
            e
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


    # --------------------------------------------------------
    # NOTIFY PROVIDER
    # --------------------------------------------------------

    if isinstance(
        result,
        dict
    ) and result.get(
        "success"
    ):

        try:

            food = database.get_food_by_id(
                food_id
            )


            if food:

                provider_id = food.get(
                    "provider_id"
                )


                if provider_id:

                    database.create_notification(

                        provider_id,

                        f"{receiver_name} requested your food: "
                        f"{food.get('food_name', '')}"

                    )

        except Exception as e:

            print(
                "PROVIDER NOTIFICATION ERROR:",
                e
            )


    if isinstance(
        result,
        dict
    ):

        return JSONResponse(
            content=result
        )


    return JSONResponse(
        content={
            "success": True,
            "message": "Food request submitted successfully."
        }
    )


# ============================================================
# REQUEST FOOD - PRIMARY API
# ============================================================

@app.post("/api/request-food")
async def api_request_food(
    request: Request
):

    return await process_food_request(
        request
    )


# ============================================================
# REQUEST FOOD - COMPATIBILITY API
# ============================================================

@app.post("/request-food")
async def request_food(
    request: Request
):

    return await process_food_request(
        request
    )


# ============================================================
# FOOD REQUESTS PAGE
# ============================================================

@app.get(
    "/food-requests",
    response_class=HTMLResponse
)
async def food_requests_page(
    request: Request
):

    user = require_login(request)

    if not user:

        return RedirectResponse(
            "/login",
            status_code=303
        )


    try:

        requests = database.get_food_requests()

    except Exception:

        requests = []


    # --------------------------------------------------------
    # ROLE-BASED REQUEST FILTER
    # --------------------------------------------------------

    if user_has_role(
        user,
        "receiver"
    ):

        filtered = []

        for item in requests:

            item = dict(item)

            if item.get("receiver_id") is not None:

                if str(item["receiver_id"]) == str(user["id"]):
                    filtered.append(item)

            elif str(item.get("receiver_name", "")).lower() == str(user["name"]).lower():
                filtered.append(item)

        requests = filtered

    elif user_has_role(
        user,
        "provider"
    ):

        # SECURITY: providers must only see requests for their own food.
        filtered = []

        for item in requests:

            item = dict(item)
            provider_id = item.get("provider_id")

            if provider_id is not None and str(provider_id) == str(user["id"]):
                filtered.append(item)

        requests = filtered


    return render_page(

        request,

        [
            "food_requests.html",
            "food-requests.html",
            "requests.html"
        ],

        {
            "user": user,
            "food_requests": requests,
            "requests": requests
        }

    )


# ============================================================
# FOOD REQUESTS API
# ============================================================

@app.get("/api/food-requests")
async def api_food_requests(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    try:

        requests = database.get_food_requests()

    except Exception:

        requests = []


    # --------------------------------------------------------
    # ROLE-BASED REQUEST FILTER
    # --------------------------------------------------------

    if user_has_role(
        user,
        "receiver"
    ):

        filtered = []

        for item in requests:

            item = dict(item)
            receiver_id = item.get("receiver_id")

            if receiver_id is not None:

                if str(receiver_id) == str(user["id"]):
                    filtered.append(item)

            elif str(item.get("receiver_name", "")).lower() == str(user["name"]).lower():
                filtered.append(item)

        requests = filtered

    elif user_has_role(
        user,
        "provider"
    ):

        # SECURITY: provider API returns only requests for their own food.
        filtered = []

        for item in requests:

            item = dict(item)
            provider_id = item.get("provider_id")

            if provider_id is not None and str(provider_id) == str(user["id"]):
                filtered.append(item)

        requests = filtered


    return {
        "success": True,
        "requests": requests
    }


# ============================================================
# UPDATE FOOD REQUEST STATUS
# ============================================================

@app.post(
    "/food-request/{request_id}/status"
)
async def update_food_request_status(
    request: Request,
    request_id: int
):

    user = require_login(request)

    if not user:
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )

    if not user_has_role(user, "provider"):
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only providers can update requests."
            }
        )

    # SECURITY: verify that this request belongs to the logged-in provider.
    conn = database.get_connection()
    try:
        row = conn.execute(
            """
            SELECT fr.id, fr.food_id, fr.status, f.provider_id
            FROM food_requests fr
            LEFT JOIN food f ON f.id = fr.food_id
            WHERE fr.id=?
            LIMIT 1
            """,
            (request_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "Food request not found."
            }
        )

    if str(row["provider_id"]) != str(user["id"]):
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "You are not authorized to modify this request."
            }
        )

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            data = await request.json()
            status = str(data.get("status", "")).strip().lower()
        else:
            form = await request.form()
            status = str(form.get("status", "")).strip().lower()
    except Exception:
        status = ""

    # Provider is allowed only to accept/reject. Collection is receiver action.
    if status not in ["accepted", "rejected"]:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Provider can only accept or reject a request."
            }
        )

    try:
        result = database.update_food_request_status(
            request_id,
            status
        )
    except Exception as e:
        print("UPDATE REQUEST STATUS ERROR:", e)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )

    # Notify the receiver after the provider decision.
    try:
        conn = database.get_connection()
        try:
            receiver = conn.execute(
                "SELECT receiver_id FROM food_requests WHERE id=? LIMIT 1",
                (request_id,)
            ).fetchone()
        finally:
            conn.close()

        if receiver and receiver["receiver_id"]:
            database.create_notification(
                receiver["receiver_id"],
                f"Your food request has been {status}."
            )
    except Exception as e:
        print("RECEIVER NOTIFICATION ERROR:", e)

    return JSONResponse(content=result)


# ============================================================
# MARK FOOD COLLECTED
# ============================================================

@app.post(
    "/food/{food_id}/collected"
)
async def mark_food_collected(
    request: Request,
    food_id: int
):

    user = require_login(request)

    if not user:
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )

    # SECURITY: only the receiver who owns the accepted request can collect it.
    conn = database.get_connection()
    try:
        row = conn.execute(
            """
            SELECT fr.id, fr.receiver_id, fr.status, f.provider_id
            FROM food_requests fr
            JOIN food f ON f.id = fr.food_id
            WHERE fr.food_id=? AND fr.status='accepted'
            ORDER BY fr.id DESC
            LIMIT 1
            """,
            (food_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": "This food does not have an accepted request."
            }
        )

    if not user_has_role(user, "receiver"):
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only the requesting receiver can collect food."
            }
        )

    if row["receiver_id"] is None or str(row["receiver_id"]) != str(user["id"]):
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "You are not authorized to collect this food."
            }
        )

    try:
        result = database.mark_food_collected(food_id)
    except Exception as e:
        print("MARK COLLECTED ERROR:", e)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )

    # Notify provider that the food was collected.
    try:
        provider_id = row["provider_id"]
        if provider_id:
            database.create_notification(
                provider_id,
                "Your food has been collected successfully."
            )
    except Exception as e:
        print("PROVIDER COLLECTION NOTIFICATION ERROR:", e)

    return JSONResponse(content=result)


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.get("/notifications")
async def notifications(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    try:

        items = database.get_notifications(
            user["id"]
        )

    except Exception:

        items = []


    return {
        "success": True,
        "notifications": items
    }


# ============================================================
# NOTIFICATION COUNT
# ============================================================

@app.get("/notifications/count")
async def notification_count(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "count": 0
            }
        )


    try:

        count = database.get_unread_notification_count(
            user["id"]
        )

    except Exception:

        count = 0


    return {
        "success": True,
        "count": count
    }


# ============================================================
# MARK NOTIFICATION READ
# ============================================================

@app.post(
    "/notifications/{notification_id}/read"
)
async def mark_notification_read(
    request: Request,
    notification_id: int
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    try:

        result = database.mark_notification_read(
            notification_id,
            user["id"]
        )

    except Exception as e:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


    return JSONResponse(
        content=result
    )


# ============================================================
# MARK ALL NOTIFICATIONS READ
# ============================================================

@app.post(
    "/notifications/read-all"
)
async def mark_all_notifications_read(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    try:

        result = database.mark_all_notifications_read(
            user["id"]
        )

    except Exception as e:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


    return JSONResponse(
        content=result
    )


# ============================================================
# SAVE RECEIVER LOCATION
# ============================================================

@app.post("/receiver-location")
async def receiver_location(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    if not user_has_role(
        user,
        "receiver"
    ):

        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only receivers can save location."
            }
        )


    # --------------------------------------------------------
    # READ JSON
    # --------------------------------------------------------

    try:

        data = await request.json()

    except Exception:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid location data."
            }
        )


    # --------------------------------------------------------
    # VALUES
    # --------------------------------------------------------

    try:

        latitude = float(
            data.get("latitude")
        )

        longitude = float(
            data.get("longitude")
        )

    except Exception:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid latitude or longitude."
            }
        )


    if latitude < -90 or latitude > 90:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid latitude."
            }
        )


    if longitude < -180 or longitude > 180:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid longitude."
            }
        )


    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid latitude or longitude."
            }
        )


    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    try:

        try:

            result = database.save_receiver_location(

                user["id"],

                user["name"],

                latitude,

                longitude

            )

        except TypeError:

            result = database.save_receiver_location(

                receiver_id=user["id"],

                receiver_name=user["name"],

                latitude=latitude,

                longitude=longitude

            )

    except Exception as e:

        print(
            "SAVE RECEIVER LOCATION ERROR:",
            e
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


    return JSONResponse(
        content={
            "success": True,
            "message": "Receiver location saved successfully.",
            "latitude": latitude,
            "longitude": longitude
        }
    )


# ============================================================
# GET RECEIVER LOCATION
# ============================================================

@app.get(
    "/receiver-location/{receiver_name}"
)
async def get_receiver_location(
    receiver_name: str
):

    try:

        location = database.get_receiver_location(
            receiver_name
        )

    except Exception:

        location = None


    if not location:

        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "Receiver location not found."
            }
        )


    return {
        "success": True,
        "location": location
    }


# ============================================================
# MY LOCATION
# ============================================================

@app.get("/my-location")
async def my_location(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    try:

        location = database.get_receiver_location(
            user["id"]
        )

    except Exception:

        location = None


    if not location:

        return {
            "success": False,
            "message": "Location not saved yet."
        }


    return {
        "success": True,
        "location": location
    }


# ============================================================
# ALL RECEIVER LOCATIONS
# ============================================================

@app.get("/receiver-locations")
async def receiver_locations(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    try:

        locations = database.get_all_receiver_locations()

    except Exception:

        locations = []


    return {
        "success": True,
        "locations": locations
    }


# ============================================================
# NEARBY RECEIVERS
# ============================================================

@app.get("/nearby-receivers")
async def nearby_receivers(
    request: Request,
    food_latitude: float = Query(...),
    food_longitude: float = Query(...),
    radius: float = Query(10)
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    if radius <= 0:

        radius = 10


    if radius > 100:

        radius = 100


    try:

        receivers = database.get_nearby_receivers(

            food_latitude,

            food_longitude,

            radius

        )

    except Exception as e:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e)
            }
        )


    return {
        "success": True,
        "radius_km": radius,
        "receivers": receivers
    }


# ============================================================
# USER API
# ============================================================

@app.get("/api/user/{user_id}")
async def api_user(
    request: Request,
    user_id: int
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    try:

        target_user = database.get_user_by_id(
            user_id
        )

    except Exception:

        target_user = None


    if not target_user:

        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "User not found."
            }
        )


    # --------------------------------------------------------
    # DON'T EXPOSE PASSWORD
    # --------------------------------------------------------

    target_user = dict(
        target_user
    )

    target_user.pop(
        "password_hash",
        None
    )


    return {
        "success": True,
        "user": target_user
    }


# ============================================================
# CURRENT USER API
# ============================================================

@app.get("/api/me")
async def api_me(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Not logged in."
            }
        )


    user = dict(user)

    user.pop(
        "password_hash",
        None
    )


    return {
        "success": True,
        "user": user
    }


# ============================================================
# RULE-BASED AI MATCHING
# EXISTING FALLBACK SYSTEM
# ============================================================

@app.get("/api/ai-matches")
async def ai_matches(
    request: Request,
    food_type: str = "Any Food",
    max_distance: float = 10,
    needed_quantity: str = ""
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    if not user_has_role(
        user,
        "receiver"
    ):

        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only receivers can use food matching."
            }
        )


    # --------------------------------------------------------
    # GET LOCATION
    # --------------------------------------------------------

    try:

        location = database.get_receiver_location(
            user["id"]
        )

    except Exception:

        location = None


    if not location:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Please share your location first."
            }
        )


    try:

        receiver_lat = float(
            location["latitude"]
        )

        receiver_lon = float(
            location["longitude"]
        )

    except Exception:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid receiver location."
            }
        )


    # --------------------------------------------------------
    # AVAILABLE FOOD
    # --------------------------------------------------------

    try:

        foods = database.get_available_food()

    except Exception:

        foods = []


    matches = []


    # --------------------------------------------------------
    # CALCULATE MATCH
    # --------------------------------------------------------

    for food in foods:

        try:

            food_lat = food.get(
                "latitude"
            )

            food_lon = food.get(
                "longitude"
            )

            if food_lat is None or food_lon is None:
                continue


            distance = database.calculate_distance_km(

                receiver_lat,

                receiver_lon,

                float(food_lat),

                float(food_lon)

            )


            if distance > max_distance:
                continue


            # ------------------------------------------------
            # DISTANCE SCORE
            # ------------------------------------------------

            distance_score = max(
                0,
                100 - (
                    distance /
                    max_distance
                ) * 100
            )


            # ------------------------------------------------
            # FOOD TYPE SCORE
            # ------------------------------------------------

            if (
                not food_type
                or food_type.lower()
                == "any food"
            ):

                type_score = 70

            elif (
                str(
                    food.get(
                        "food_type",
                        ""
                    )
                ).lower()
                == food_type.lower()
            ):

                type_score = 100

            else:

                type_score = 25


            # ------------------------------------------------
            # FINAL SCORE
            # ------------------------------------------------

            score = (

                type_score * 0.6

                +

                distance_score * 0.4

            )


            matches.append({

                "food_id": food["id"],

                "food_name": food["food_name"],

                "food_type": food["food_type"],

                "quantity": food["quantity"],

                "provider_name": food["provider_name"],

                "distance_km": round(
                    distance,
                    2
                ),

                "latitude": food_lat,

                "longitude": food_lon,

                "score": round(
                    score
                ),

                "reason":
                    "Matched using food type "
                    "and distance."

            })

        except Exception as e:

            print(
                "MATCH ERROR:",
                e
            )


    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    matches.sort(
        key=lambda item: item["score"],
        reverse=True
    )


    return {

        "success": True,

        "ai_enabled": False,

        "matches": matches

    }


# ============================================================
# REAL GEMINI AI AGENT MATCHING
# ============================================================

@app.get("/api/ai-agent-matches")
async def ai_agent_matches(
    request: Request,
    food_type: str = "Any Food",
    max_distance: float = 10,
    needed_quantity: str = ""
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    if not user_has_role(
        user,
        "receiver"
    ):

        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message":
                    "Only receivers can use the AI agent."
            }
        )


    # --------------------------------------------------------
    # VALIDATE DISTANCE
    # --------------------------------------------------------

    try:

        max_distance = float(
            max_distance
        )

    except Exception:

        max_distance = 10


    if max_distance <= 0:

        max_distance = 10


    if max_distance > 50:

        max_distance = 50


    # --------------------------------------------------------
    # AI AGENT CHECK
    # --------------------------------------------------------

    if not AI_AGENT_AVAILABLE:

        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "ai_enabled": False,
                "message":
                    "AI agent is not available. "
                    "Check ai_agent.py and installed packages."
            }
        )


    # --------------------------------------------------------
    # RUN GEMINI AGENT
    # --------------------------------------------------------

    try:

        result = run_food_matching_agent(

            receiver_id=user["id"],

            food_type=food_type,

            max_distance_km=max_distance,

            needed_quantity=needed_quantity

        )

    except Exception as e:

        print(
            "AI AGENT ERROR:",
            e
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "ai_enabled": True,
                "message":
                    "AI matching agent failed.",
                "error": str(e)
            }
        )


    # --------------------------------------------------------
    # RETURN RESULT
    # --------------------------------------------------------

    return JSONResponse(
        content=result
    )


# ============================================================
# PHASE 3.3 - AI RECEIVER SELECTION AGENT (PRESERVED)
# ============================================================

@app.post("/api/ai-receiver-matches")
async def ai_receiver_matches(
    request: Request,
    food_id: int,
    max_distance: float = 10,
):
    user = require_login(request)

    if not user:
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first.",
            },
        )

    if not user_has_role(user, "provider"):
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only providers can use the receiver selection agent.",
            },
        )

    # SECURITY: a provider may only ask the agent about food owned by them.
    try:
        food_id = int(food_id)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid food ID.",
            },
        )

    if food_id <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Invalid food ID.",
            },
        )

    food = database.get_food_by_id(food_id)
    if not food:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "Food listing not found.",
            },
        )

    food = dict(food)
    provider_id = food.get("provider_id")
    if provider_id is None or str(provider_id) != str(user["id"]):
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "You can only select receivers for your own food listings.",
            },
        )

    try:
        max_distance = float(max_distance)
    except Exception:
        max_distance = 10

    if max_distance <= 0:
        max_distance = 10
    if max_distance > 50:
        max_distance = 50

    if not AI_AGENT_AVAILABLE:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "ai_enabled": False,
                "message": "AI agent is not available. Check ai_agent.py and installed packages.",
            },
        )

    try:
        result = run_receiver_selection_agent(
            food_id=food_id,
            max_distance_km=max_distance,
        )
    except Exception as exc:
        print("AI RECEIVER SELECTION ERROR:", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "ai_enabled": True,
                "message": "Receiver selection agent failed.",
                "error": str(exc),
            },
        )

    return JSONResponse(content=result)


# ============================================================
# PHASE 3.4 - AI NOTIFICATION AGENT
# ============================================================

@app.post("/api/ai-notify-receivers")
async def ai_notify_receivers(
    request: Request,
    food_id: int,
    max_distance: float = 10,
    max_notifications: int = 3,
):
    user = require_login(request)

    if not user:
        return JSONResponse(status_code=401, content={
            "success": False,
            "message": "Please login first.",
        })

    if not user_has_role(user, "provider"):
        return JSONResponse(status_code=403, content={
            "success": False,
            "message": "Only providers can trigger AI receiver notifications.",
        })

    try:
        food_id = int(food_id)
    except Exception:
        return JSONResponse(status_code=400, content={
            "success": False,
            "message": "Invalid food ID.",
        })

    if food_id <= 0:
        return JSONResponse(status_code=400, content={
            "success": False,
            "message": "Invalid food ID.",
        })

    food = database.get_food_by_id(food_id)
    if not food:
        return JSONResponse(status_code=404, content={
            "success": False,
            "message": "Food listing not found.",
        })

    food = dict(food)

    # SECURITY: provider can only notify receivers about their own food.
    provider_id = food.get("provider_id")
    if provider_id is None or str(provider_id) != str(user["id"]):
        return JSONResponse(status_code=403, content={
            "success": False,
            "message": "You can only send AI notifications for your own food listings.",
        })

    try:
        max_distance = float(max_distance)
    except Exception:
        max_distance = 10
    max_distance = max(0.1, min(max_distance, 50))

    try:
        max_notifications = int(max_notifications)
    except Exception:
        max_notifications = 3
    max_notifications = max(1, min(max_notifications, 5))

    if not NOTIFICATION_AGENT_AVAILABLE or run_notification_agent is None:
        return JSONResponse(status_code=503, content={
            "success": False,
            "message": "Notification Agent is not available.",
        })

    try:
        result = run_notification_agent(
            food_id=food_id,
            max_distance_km=max_distance,
            max_notifications=max_notifications,
        )
    except Exception as exc:
        print("AI NOTIFICATION AGENT ERROR:", exc)
        return JSONResponse(status_code=500, content={
            "success": False,
            "message": "AI notification agent failed.",
            "error": str(exc),
        })

    return JSONResponse(content=result)


# ============================================================
# PHASE 3.5 - AI EXPIRY / RESCUE AGENT
# ============================================================

@app.post("/api/ai-expiry-rescue")
async def ai_expiry_rescue(
    request: Request,
    food_id: int,
    max_distance: float = 10,
    notify: bool = True,
):
    user = require_login(request)

    if not user:
        return JSONResponse(status_code=401, content={
            "success": False,
            "message": "Please login first.",
        })

    if not user_has_role(user, "provider"):
        return JSONResponse(status_code=403, content={
            "success": False,
            "message": "Only providers can use the Expiry / Rescue Agent.",
        })

    try:
        food_id = int(food_id)
    except Exception:
        return JSONResponse(status_code=400, content={
            "success": False,
            "message": "Invalid food ID.",
        })

    if food_id <= 0:
        return JSONResponse(status_code=400, content={
            "success": False,
            "message": "Invalid food ID.",
        })

    food = database.get_food_by_id(food_id)
    if not food:
        return JSONResponse(status_code=404, content={
            "success": False,
            "message": "Food listing not found.",
        })

    food = dict(food)
    provider_id = food.get("provider_id")
    if provider_id is None or str(provider_id) != str(user["id"]):
        return JSONResponse(status_code=403, content={
            "success": False,
            "message": "You can only analyze your own food listings.",
        })

    try:
        max_distance = float(max_distance)
    except Exception:
        max_distance = 10
    max_distance = max(0.1, min(max_distance, 50))

    if not EXPIRY_RESCUE_AGENT_AVAILABLE or analyze_food_expiry is None:
        return JSONResponse(status_code=503, content={
            "success": False,
            "message": "Expiry / Rescue Agent is not available.",
        })

    try:
        result = analyze_food_expiry(
            food_id=food_id,
            max_distance_km=max_distance,
            notify=bool(notify),
        )
    except Exception as exc:
        print("AI EXPIRY / RESCUE ERROR:", exc)
        return JSONResponse(status_code=500, content={
            "success": False,
            "message": "Expiry / Rescue Agent failed.",
            "error": str(exc),
        })

    return JSONResponse(content=result)


@app.post("/api/ai-expiry-rescue-scan")
async def ai_expiry_rescue_scan(
    request: Request,
    max_distance: float = 10,
    notify: bool = True,
):
    user = require_login(request)

    if not user:
        return JSONResponse(status_code=401, content={
            "success": False,
            "message": "Please login first.",
        })

    if not user_has_role(user, "provider"):
        return JSONResponse(status_code=403, content={
            "success": False,
            "message": "Only providers can run the Expiry / Rescue scan.",
        })

    try:
        max_distance = float(max_distance)
    except Exception:
        max_distance = 10
    max_distance = max(0.1, min(max_distance, 50))

    if not EXPIRY_RESCUE_AGENT_AVAILABLE or scan_provider_foods is None:
        return JSONResponse(status_code=503, content={
            "success": False,
            "message": "Expiry / Rescue Agent is not available.",
        })

    try:
        result = scan_provider_foods(
            provider_id=user["id"],
            max_distance_km=max_distance,
            notify=bool(notify),
        )
    except Exception as exc:
        print("AI EXPIRY / RESCUE SCAN ERROR:", exc)
        return JSONResponse(status_code=500, content={
            "success": False,
            "message": "Expiry / Rescue scan failed.",
            "error": str(exc),
        })

    return JSONResponse(content=result)


# ============================================================
# PHASE 3.6 - MULTI-AGENT WORKFLOW
# ============================================================

@app.post("/api/ai-multi-agent-workflow")
async def ai_multi_agent_workflow(
    request: Request,
    food_id: int,
    max_distance: float = 10,
    max_notifications: int = 3,
):
    user = require_login(request)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Please login first."})
    if not user_has_role(user, "provider"):
        return JSONResponse(status_code=403, content={"success": False, "message": "Only providers can run the multi-agent workflow."})
    try:
        food_id = int(food_id)
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid food ID."})
    if food_id <= 0:
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid food ID."})
    food = database.get_food_by_id(food_id)
    if not food:
        return JSONResponse(status_code=404, content={"success": False, "message": "Food listing not found."})
    food = dict(food)
    if food.get("provider_id") is None or str(food.get("provider_id")) != str(user["id"]):
        return JSONResponse(status_code=403, content={"success": False, "message": "You can only run the workflow for your own food listings."})
    if not MULTI_AGENT_WORKFLOW_AVAILABLE or run_multi_agent_workflow is None:
        return JSONResponse(status_code=503, content={"success": False, "message": "Multi-Agent Workflow is not available."})
    try:
        max_distance = max(0.1, min(float(max_distance), 50))
    except Exception:
        max_distance = 10
    try:
        max_notifications = max(1, min(int(max_notifications), 5))
    except Exception:
        max_notifications = 3
    try:
        result = run_multi_agent_workflow(
            food_id=food_id,
            provider_id=int(user["id"]),
            max_distance_km=max_distance,
            max_notifications=max_notifications,
        )
    except Exception as exc:
        print("AI MULTI-AGENT WORKFLOW ERROR:", exc)
        return JSONResponse(status_code=500, content={"success": False, "message": "Multi-Agent Workflow failed.", "error": str(exc)})
    return JSONResponse(content=result)


@app.post("/api/ai-multi-agent-workflow-scan")
async def ai_multi_agent_workflow_scan(
    request: Request,
    max_distance: float = 10,
    max_notifications: int = 3,
):
    user = require_login(request)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Please login first."})
    if not user_has_role(user, "provider"):
        return JSONResponse(status_code=403, content={"success": False, "message": "Only providers can run the multi-agent workflow."})
    if not MULTI_AGENT_WORKFLOW_AVAILABLE or run_provider_workflow_scan is None:
        return JSONResponse(status_code=503, content={"success": False, "message": "Multi-Agent Workflow is not available."})
    try:
        max_distance = max(0.1, min(float(max_distance), 50))
    except Exception:
        max_distance = 10
    try:
        max_notifications = max(1, min(int(max_notifications), 5))
    except Exception:
        max_notifications = 3
    try:
        result = run_provider_workflow_scan(
            provider_id=int(user["id"]),
            max_distance_km=max_distance,
            max_notifications=max_notifications,
        )
    except Exception as exc:
        print("AI MULTI-AGENT WORKFLOW SCAN ERROR:", exc)
        return JSONResponse(status_code=500, content={"success": False, "message": "Multi-Agent Workflow scan failed.", "error": str(exc)})
    return JSONResponse(content=result)


# ============================================================
# PHASE 3.7 - AGENT DASHBOARD
# ============================================================

@app.get("/api/agent-dashboard")
async def api_agent_dashboard(request: Request):

    user = require_login(request)

    if not user:
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )

    if not user_has_role(user, "provider"):
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": "Only providers can view the Agent Dashboard."
            }
        )

    if not AGENT_DASHBOARD_AVAILABLE or get_agent_dashboard is None:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "message": "Agent Dashboard is unavailable."
            }
        )

    try:
        result = get_agent_dashboard(int(user["id"]))
    except Exception as exc:
        print("AGENT DASHBOARD ERROR:", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Agent Dashboard failed.",
                "error": str(exc)
            }
        )

    if not result.get("success"):
        return JSONResponse(status_code=500, content=result)

    return JSONResponse(content=result)


# ============================================================
# PHASE 4 - ADMIN & IMPACT ANALYTICS
# ============================================================

try:
    from admin_dashboard import get_admin_dashboard, get_admin_trends, get_admin_location_analytics, get_admin_performance_analytics, get_admin_impact_report, get_admin_date_range_report, get_admin_control_center, get_admin_data_quality, get_admin_user_directory, is_admin_user
    ADMIN_DASHBOARD_AVAILABLE = True
except Exception as e:
    print("ADMIN DASHBOARD IMPORT ERROR:", e)
    get_admin_dashboard = None
    get_admin_trends = None
    get_admin_location_analytics = None
    get_admin_performance_analytics = None
    get_admin_impact_report = None
    get_admin_date_range_report = None
    get_admin_control_center = None
    get_admin_data_quality = None
    get_admin_user_directory = None
    is_admin_user = None
    ADMIN_DASHBOARD_AVAILABLE = False


def require_admin(request: Request):
    user = require_login(request)
    if not user:
        return None

    # Phase 4 admin authorization uses the separate admin_users table.
    # Do NOT require user["role"] == "admin" because the existing
    # users table intentionally allows only provider/receiver.
    if not ADMIN_DASHBOARD_AVAILABLE or is_admin_user is None:
        return None

    try:
        if not is_admin_user(user):
            return None
    except Exception:
        return None

    return user


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    user = require_login(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if not ADMIN_DASHBOARD_AVAILABLE or is_admin_user is None:
        return HTMLResponse("<h2>Admin Dashboard unavailable.</h2>", status_code=503)

    try:
        if not is_admin_user(user):
            return HTMLResponse("<h2>403 - Admin access required.</h2>", status_code=403)
    except Exception:
        return HTMLResponse("<h2>403 - Admin access required.</h2>", status_code=403)

    return render_page(request, ["admin.html"], {"user": user})


@app.get("/api/admin/dashboard")
async def api_admin_dashboard(request: Request):
    user = require_admin(request)
    if not user:
        return JSONResponse(status_code=403, content={"success": False, "message": "Admin access required."})
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_dashboard is None:
        return JSONResponse(status_code=503, content={"success": False, "message": "Admin Dashboard unavailable."})
    result = get_admin_dashboard()
    if not result.get("success"):
        return JSONResponse(status_code=500, content=result)
    return JSONResponse(content=result)


@app.get("/api/admin/trends")
async def api_admin_trends(
    request: Request,
    start_date: str = "",
    end_date: str = "",
):
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)

    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_trends is None:
        return JSONResponse({"success": False, "message": "Advanced analytics unavailable."}, status_code=503)

    # Keep query parameters bounded and simple.
    if len(start_date) > 10 or len(end_date) > 10:
        return JSONResponse({"success": False, "message": "Invalid date range."}, status_code=400)

    try:
        result = get_admin_trends(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Trend analytics failed: {exc}"}, status_code=500)


@app.get("/api/admin/location-analytics")
async def api_admin_location_analytics(
    request: Request,
    start_date: str = "",
    end_date: str = "",
    top_n: int = 12,
):
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)

    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_location_analytics is None:
        return JSONResponse({"success": False, "message": "Location analytics unavailable."}, status_code=503)

    if len(start_date) > 10 or len(end_date) > 10:
        return JSONResponse({"success": False, "message": "Invalid date range."}, status_code=400)
    if top_n < 3 or top_n > 50:
        return JSONResponse({"success": False, "message": "top_n must be between 3 and 50."}, status_code=400)

    try:
        result = get_admin_location_analytics(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
            top_n=top_n,
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Location analytics failed: {exc}"}, status_code=500)



@app.get("/api/admin/performance")
async def api_admin_performance(
    request: Request,
    start_date: str = "",
    end_date: str = "",
    top_n: int = 20,
):
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_performance_analytics is None:
        return JSONResponse({"success": False, "message": "Performance analytics unavailable."}, status_code=503)
    if len(start_date) > 10 or len(end_date) > 10:
        return JSONResponse({"success": False, "message": "Invalid date range."}, status_code=400)
    if top_n < 5 or top_n > 100:
        return JSONResponse({"success": False, "message": "top_n must be between 5 and 100."}, status_code=400)
    try:
        result = get_admin_performance_analytics(start_date=start_date.strip() or None,end_date=end_date.strip() or None,top_n=top_n)
        return JSONResponse(result, headers={"Cache-Control":"no-store"})
    except ValueError as exc:
        return JSONResponse({"success":False,"message":str(exc)},status_code=400)
    except Exception as exc:
        return JSONResponse({"success":False,"message":f"Performance analytics failed: {exc}"},status_code=500)


@app.get("/api/admin/report")
async def api_admin_report(
    request: Request,
    start_date: str = "",
    end_date: str = "",
    top_n: int = 20,
):
    """
    Phase 4.5: unified read-only impact report.
    """
    user = require_admin(request)
    if not user:
        return JSONResponse(
            {"success": False, "message": "Admin access required."},
            status_code=403,
        )

    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_impact_report is None:
        return JSONResponse(
            {"success": False, "message": "Impact reporting unavailable."},
            status_code=503,
        )

    if len(start_date) > 10 or len(end_date) > 10:
        return JSONResponse(
            {"success": False, "message": "Invalid date range."},
            status_code=400,
        )

    if top_n < 5 or top_n > 100:
        return JSONResponse(
            {"success": False, "message": "top_n must be between 5 and 100."},
            status_code=400,
        )

    try:
        result = get_admin_impact_report(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
            top_n=top_n,
        )
        if not result.get("success"):
            return JSONResponse(result, status_code=500)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        return JSONResponse(
            {"success": False, "message": str(exc)},
            status_code=400,
        )
    except Exception as exc:
        return JSONResponse(
            {"success": False, "message": f"Impact report failed: {exc}"},
            status_code=500,
        )


def _csv_escape(value):
    text = "" if value is None else str(value)
    if any(ch in text for ch in [",", '"', "\n", "\r"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def _csv_response(filename, headers, rows):
    lines = [",".join(_csv_escape(h) for h in headers)]
    for row in rows:
        lines.append(",".join(_csv_escape(v) for v in row))
    body = "\n".join(lines) + "\n"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )



@app.get("/api/admin/date-range-report")
async def api_admin_date_range_report(
    request: Request,
    start_date: str = "",
    end_date: str = "",
    preset: str = "",
    compare: bool = True,
    top_n: int = 20,
):
    """Phase 4.6: admin-only date-range report and period comparison."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_date_range_report is None:
        return JSONResponse({"success": False, "message": "Date-range reporting unavailable."}, status_code=503)
    if len(start_date) > 10 or len(end_date) > 10 or len(preset) > 30:
        return JSONResponse({"success": False, "message": "Invalid date-range parameters."}, status_code=400)
    if top_n < 5 or top_n > 100:
        return JSONResponse({"success": False, "message": "top_n must be between 5 and 100."}, status_code=400)
    try:
        result = get_admin_date_range_report(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
            preset=preset.strip() or None,
            compare=bool(compare),
            top_n=top_n,
        )
        if not result.get("success"):
            return JSONResponse(result, status_code=500)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Date-range report failed: {exc}"}, status_code=500)


@app.get("/api/admin/export/date-range.csv")
async def api_admin_export_date_range(
    request: Request,
    start_date: str = "",
    end_date: str = "",
    preset: str = "",
):
    """Phase 4.6 CSV export for the selected date-range report."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_date_range_report is None:
        return JSONResponse({"success": False, "message": "Date-range reporting unavailable."}, status_code=503)
    if len(start_date) > 10 or len(end_date) > 10 or len(preset) > 30:
        return JSONResponse({"success": False, "message": "Invalid date-range parameters."}, status_code=400)
    try:
        report = get_admin_date_range_report(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
            preset=preset.strip() or None,
            compare=True,
            top_n=20,
        )
        if not report.get("success"):
            return JSONResponse(report, status_code=500)
        s = report.get("summary", {})
        p = report.get("previous_summary") or {}
        rows = [
            ["Report", "FoodLoop AI Phase 4.6 Date-Range Report"],
            ["Range", report.get("range", {}).get("label", "")],
            ["Start Date", report.get("range", {}).get("start", "")],
            ["End Date", report.get("range", {}).get("end", "")],
            ["Days", report.get("range", {}).get("days", "")],
            [],
            ["Metric", "Current", "Previous", "Change %"],
        ]
        for key, label in [
            ("food_listings", "Food Listings"), ("food_rescued", "Food Rescued"),
            ("food_expired", "Food Expired"), ("quantity_posted", "Quantity Posted"),
            ("quantity_rescued", "Quantity Rescued"), ("requests", "Requests"),
            ("collected_requests", "Collected Requests"), ("collection_rate", "Collection Rate %"),
            ("new_users", "New Users"), ("ai_notifications", "AI Notifications"),
            ("rescue_rate", "Rescue Rate %"),
        ]:
            rows.append([label, s.get(key, 0), p.get(key, 0), report.get("comparison", {}).get(key, {}).get("change_percent", 0)])
        rows.append([])
        rows.append(["Date", "Posted", "Rescued", "Requests", "Collected Requests"])
        for item in report.get("daily", []):
            rows.append([item.get("date"), item.get("posted", 0), item.get("rescued", 0), item.get("requests", 0), item.get("collected", 0)])
        return _csv_response("foodloop_phase4_6_date_range_report.csv", rows[0], rows[1:])
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Date-range CSV export failed: {exc}"}, status_code=500)

@app.get("/api/admin/export/performance.csv")
async def api_admin_export_performance(
    request: Request,
    start_date: str = "",
    end_date: str = "",
):
    """
    Phase 4.5 CSV export for provider and receiver performance.
    """
    user = require_admin(request)
    if not user:
        return JSONResponse(
            {"success": False, "message": "Admin access required."},
            status_code=403,
        )

    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_performance_analytics is None:
        return JSONResponse(
            {"success": False, "message": "Performance analytics unavailable."},
            status_code=503,
        )

    if len(start_date) > 10 or len(end_date) > 10:
        return JSONResponse(
            {"success": False, "message": "Invalid date range."},
            status_code=400,
        )

    try:
        data = get_admin_performance_analytics(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
            top_n=100,
        )
        if not data.get("success"):
            return JSONResponse(data, status_code=500)

        rows = []
        for item in data.get("providers", []):
            rows.append([
                "Provider",
                item.get("provider"),
                item.get("email"),
                item.get("food_listings"),
                item.get("rescued"),
                item.get("expired"),
                item.get("requests"),
                item.get("collected_requests"),
                item.get("rescue_rate"),
                item.get("request_conversion"),
                item.get("performance_score"),
            ])

        for item in data.get("receivers", []):
            rows.append([
                "Receiver",
                item.get("receiver"),
                item.get("email"),
                "",
                "",
                "",
                item.get("requests"),
                item.get("collected"),
                item.get("success_rate"),
                item.get("acceptance_rate"),
                item.get("performance_score"),
            ])

        headers = [
            "Role",
            "Name",
            "Email",
            "Food Listings",
            "Rescued Food",
            "Expired Food",
            "Requests",
            "Collected",
            "Rescue/Success Rate %",
            "Conversion/Acceptance Rate %",
            "Performance Score",
        ]
        return _csv_response(
            "foodloop_provider_receiver_performance.csv",
            headers,
            rows,
        )
    except Exception as exc:
        return JSONResponse(
            {"success": False, "message": f"CSV export failed: {exc}"},
            status_code=500,
        )


@app.get("/api/admin/export/trends.csv")
async def api_admin_export_trends(
    request: Request,
    start_date: str = "",
    end_date: str = "",
):
    """
    Phase 4.5 CSV export for monthly trend analytics.
    """
    user = require_admin(request)
    if not user:
        return JSONResponse(
            {"success": False, "message": "Admin access required."},
            status_code=403,
        )

    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_trends is None:
        return JSONResponse(
            {"success": False, "message": "Trend analytics unavailable."},
            status_code=503,
        )

    if len(start_date) > 10 or len(end_date) > 10:
        return JSONResponse(
            {"success": False, "message": "Invalid date range."},
            status_code=400,
        )

    try:
        data = get_admin_trends(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
        )
        if not data.get("success"):
            return JSONResponse(data, status_code=500)

        rows = []
        labels = data.get("labels", []) or []
        series = [
            ("food_posted", (data.get("food", {}) or {}).get("posted", [])),
            ("food_rescued", (data.get("food", {}) or {}).get("rescued", [])),
            ("food_expired", (data.get("food", {}) or {}).get("expired", [])),
            ("rescued_quantity", (data.get("food", {}) or {}).get("rescued_quantity", [])),
            ("requests_total", (data.get("requests", {}) or {}).get("total", [])),
            ("requests_collected", (data.get("requests", {}) or {}).get("collected", [])),
            ("requests_pending", (data.get("requests", {}) or {}).get("pending", [])),
            ("users_registered", (data.get("users", {}) or {}).get("registered", [])),
            ("notifications_total", (data.get("notifications", {}) or {}).get("total", [])),
            ("notifications_ai", (data.get("notifications", {}) or {}).get("ai", [])),
        ]
        for metric, values in series:
            for index, label in enumerate(labels):
                value = values[index] if index < len(values) else 0
                rows.append([metric, label, value])

        headers = ["Metric", "Period", "Value"]
        return _csv_response(
            "foodloop_monthly_trends.csv",
            headers,
            rows,
        )
    except Exception as exc:
        return JSONResponse(
            {"success": False, "message": f"Trend CSV export failed: {exc}"},
            status_code=500,
        )


@app.get("/api/admin/export/summary.csv")
async def api_admin_export_summary(
    request: Request,
    start_date: str = "",
    end_date: str = "",
):
    """
    Phase 4.5 one-page impact summary CSV.
    """
    user = require_admin(request)
    if not user:
        return JSONResponse(
            {"success": False, "message": "Admin access required."},
            status_code=403,
        )

    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_impact_report is None:
        return JSONResponse(
            {"success": False, "message": "Impact reporting unavailable."},
            status_code=503,
        )

    if len(start_date) > 10 or len(end_date) > 10:
        return JSONResponse(
            {"success": False, "message": "Invalid date range."},
            status_code=400,
        )

    try:
        report = get_admin_impact_report(
            start_date=start_date.strip() or None,
            end_date=end_date.strip() or None,
            top_n=20,
        )
        if not report.get("success"):
            return JSONResponse(report, status_code=500)

        dashboard = report.get("dashboard", {})
        food = dashboard.get("food", {})
        users = dashboard.get("users", {})
        requests = dashboard.get("requests", {})
        notifications = dashboard.get("notifications", {})
        performance = report.get("performance", {}).get("summary", {})
        location = report.get("location", {}).get("summary", {})

        rows = [
            ["Report Generated", report.get("generated_at", "")],
            ["Start Date", start_date or "All"],
            ["End Date", end_date or "All"],
            ["Total Food Listings", food.get("total", 0)],
            ["Available Food", food.get("available", 0)],
            ["Food Rescued", food.get("collected", 0)],
            ["Rescue Queue", food.get("rescue", 0)],
            ["Critical Food", food.get("critical", 0)],
            ["Total Requests", requests.get("total", 0)],
            ["Pending Requests", requests.get("pending", 0)],
            ["Collected Requests", requests.get("collected", 0)],
            ["Collection Success Rate %", requests.get("success_rate", 0)],
            ["Providers", users.get("providers", 0)],
            ["Receivers", users.get("receivers", 0)],
            ["AI Notifications", notifications.get("ai", 0)],
            ["Geo-tagged Food", location.get("geo_tagged_food", 0)],
            ["Receiver Locations", location.get("receiver_locations", 0)],
            ["Performance Providers", performance.get("providers", 0)],
            ["Performance Receivers", performance.get("receivers", 0)],
            ["Top Provider", performance.get("top_provider", "")],
            ["Top Receiver", performance.get("top_receiver", "")],
            ["Overall Collection Rate %", performance.get("overall_collection_rate", 0)],
        ]
        return _csv_response(
            "foodloop_impact_summary.csv",
            ["Metric", "Value"],
            rows,
        )
    except Exception as exc:
        return JSONResponse(
            {"success": False, "message": f"Summary CSV export failed: {exc}"},
            status_code=500,
        )


@app.get("/api/admin/impact")
async def api_admin_impact(request: Request):
    return await api_admin_dashboard(request)



# ============================================================
# PHASE 4.7 - ADMIN CONTROL CENTER & IMPACT INTELLIGENCE
# ============================================================

@app.get("/api/admin/control-center")
async def api_admin_control_center(request: Request):
    """Admin-only read-only system, data-quality and impact overview."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_control_center is None:
        return JSONResponse({"success": False, "message": "Admin control center unavailable."}, status_code=503)
    try:
        result = get_admin_control_center()
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Control center failed: {exc}"}, status_code=500)


@app.get("/api/admin/data-quality")
async def api_admin_data_quality(request: Request):
    """Admin-only read-only data quality audit."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_data_quality is None:
        return JSONResponse({"success": False, "message": "Data quality audit unavailable."}, status_code=503)
    try:
        result = get_admin_data_quality()
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Data quality audit failed: {exc}"}, status_code=500)


@app.get("/api/admin/users")
async def api_admin_users(
    request: Request,
    search: str = "",
    role: str = "",
    limit: int = 100,
):
    """Admin-only read-only searchable user directory."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_user_directory is None:
        return JSONResponse({"success": False, "message": "User directory unavailable."}, status_code=503)
    if len(search) > 100 or len(role) > 20:
        return JSONResponse({"success": False, "message": "Invalid search parameters."}, status_code=400)
    role = role.strip().lower()
    if role and role not in {"provider", "receiver"}:
        return JSONResponse({"success": False, "message": "Role must be provider or receiver."}, status_code=400)
    if limit < 1 or limit > 500:
        return JSONResponse({"success": False, "message": "limit must be between 1 and 500."}, status_code=400)
    try:
        result = get_admin_user_directory(search=search.strip(), role=role, limit=limit)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"User directory failed: {exc}"}, status_code=500)


@app.get("/api/admin/export/impact.csv")
async def api_admin_export_impact(request: Request):
    """Phase 4.7 CSV export for the admin impact/control-center summary."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_control_center is None:
        return JSONResponse({"success": False, "message": "Impact export unavailable."}, status_code=503)
    try:
        report = get_admin_control_center()
        if not report.get("success"):
            return JSONResponse(report, status_code=500)
        i = report.get("impact", {})
        system = report.get("system", {})
        rows = [
            ["FoodLoop AI", "Phase 4.7 Admin Impact Report"],
            ["Generated At", report.get("generated_at", "")],
            [],
            ["Metric", "Value"],
            ["Impact Index", i.get("impact_index", 0)],
            ["Food Listings", i.get("food_listings", 0)],
            ["Available Food", i.get("food_available", 0)],
            ["Food Rescued", i.get("food_rescued", 0)],
            ["Food Expired", i.get("food_expired", 0)],
            ["Quantity Posted", i.get("quantity_posted", 0)],
            ["Quantity Rescued", i.get("quantity_rescued", 0)],
            ["Estimated Meals Rescued", i.get("estimated_meals_rescued", 0)],
            ["Rescue Rate %", i.get("rescue_rate", 0)],
            ["Total Requests", i.get("request_total", 0)],
            ["Collected Requests", i.get("requests_collected", 0)],
            ["Accepted Requests", i.get("requests_accepted", 0)],
            ["Rejected Requests", i.get("requests_rejected", 0)],
            ["Pending Requests", i.get("requests_pending", 0)],
            ["Collection Rate %", i.get("collection_rate", 0)],
            ["Providers", i.get("providers", 0)],
            ["Receivers", i.get("receivers", 0)],
            ["Receiver Locations", i.get("receiver_locations", 0)],
            ["AI Notifications", i.get("ai_notifications", 0)],
            ["System Score", system.get("system_score", 0)],
            ["Data Quality Critical", system.get("data_quality_critical", 0)],
            ["Data Quality Warnings", system.get("data_quality_warnings", 0)],
        ]
        return _csv_response("foodloop_phase4_7_impact.csv", rows[0], rows[1:])
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Impact CSV export failed: {exc}"}, status_code=500)


@app.get("/api/admin/export/users.csv")
async def api_admin_export_users(request: Request):
    """Phase 4.7 CSV export for the read-only user directory."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"success": False, "message": "Admin access required."}, status_code=403)
    if not ADMIN_DASHBOARD_AVAILABLE or get_admin_user_directory is None:
        return JSONResponse({"success": False, "message": "User export unavailable."}, status_code=503)
    try:
        result = get_admin_user_directory(limit=500)
        rows = [["ID", "Name", "Email", "Role", "Created At"]]
        for item in result.get("users", []):
            rows.append([item.get("id", ""), item.get("name", ""), item.get("email", ""), item.get("role", ""), item.get("created_at", "")])
        return _csv_response("foodloop_phase4_7_users.csv", rows[0], rows[1:])
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"User CSV export failed: {exc}"}, status_code=500)

# ============================================================
# AI AGENT STATUS
# ============================================================

@app.get("/api/ai-agent-status")
async def ai_agent_status(
    request: Request
):

    user = require_login(request)

    if not user:

        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Please login first."
            }
        )


    gemini_key_exists = bool(
        os.getenv("GEMINI_API_KEY")
    )


    return {

        "success": True,

        "ai_agent_available":
            AI_AGENT_AVAILABLE,

        "notification_agent_available":
            NOTIFICATION_AGENT_AVAILABLE,

        "expiry_rescue_agent_available":
            EXPIRY_RESCUE_AGENT_AVAILABLE,

        "multi_agent_workflow_available":
            MULTI_AGENT_WORKFLOW_AVAILABLE,

        "gemini_api_key_configured":
            gemini_key_exists,

        "admin_dashboard_available":
            ADMIN_DASHBOARD_AVAILABLE

    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():

    return {

        "status": "ok",

        "application": "FoodLoop AI",

        "ai_agent":
            AI_AGENT_AVAILABLE,

        "notification_agent":
            NOTIFICATION_AGENT_AVAILABLE,

        "expiry_rescue_agent":
            EXPIRY_RESCUE_AGENT_AVAILABLE,

        "multi_agent_workflow":
            MULTI_AGENT_WORKFLOW_AVAILABLE,

        "admin_dashboard":
            ADMIN_DASHBOARD_AVAILABLE

    }


# ============================================================
# STARTUP MESSAGE
# ============================================================

@app.on_event("startup")
async def startup_event():

    print("")
    print("==============================================")
    print("          FOODLOOP AI STARTED")
    print("==============================================")
    print(
        "AI Agent Available:",
        AI_AGENT_AVAILABLE
    )
    print(
        "Notification Agent Available:",
        NOTIFICATION_AGENT_AVAILABLE
    )
    print(
        "Expiry / Rescue Agent Available:",
        EXPIRY_RESCUE_AGENT_AVAILABLE
    )
    print(
        "Multi-Agent Workflow Available:",
        MULTI_AGENT_WORKFLOW_AVAILABLE
    )
    print(
        "Agent Dashboard Available:",
        AGENT_DASHBOARD_AVAILABLE
    )
    print(
        "Gemini API Key:",
        bool(
            os.getenv("GEMINI_API_KEY")
        )
    )
    print("==============================================")
    print("")


# ============================================================
# RUN DIRECTLY
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )