# ============================================================
# FOODLOOP AI - PHASE 3.2 INTELLIGENT FOOD MATCHING AGENT
# ============================================================

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

import database

load_dotenv()

try:
    from google import genai
    from google.genai import types
except Exception as exc:
    genai = None
    types = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = None


MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Phase 3.2 deterministic scoring weights.
SCORE_WEIGHTS = {
    "food_type": 25,
    "distance": 25,
    "quantity": 20,
    "urgency": 20,
    "pickup": 10,
}


def _to_dict(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value)
    except Exception:
        return value


def _quantity_number(value):
    if value is None:
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _quantity_compatible(food_quantity, needed_quantity):
    if not str(needed_quantity or "").strip():
        return True

    requested = _quantity_number(needed_quantity)
    available = _quantity_number(food_quantity)

    # Units/numbers cannot always be compared safely. Do not reject solely
    # because the text cannot be parsed.
    if requested is None or available is None:
        return True

    return available >= requested


def _normalize_food_type(value):
    """Normalize common food-type labels into stable matching categories."""
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower())
    text = re.sub(r"\s+", " ", text).strip()

    if text in ("", "any", "any food", "all", "all food", "other"):
        return "any"

    vegetarian_values = {
        "veg",
        "vegetarian",
        "vegetarian food",
        "pure veg",
        "pure vegetarian",
        "veg food",
    }
    non_vegetarian_values = {
        "non veg",
        "non vegetarian",
        "non vegetarian food",
        "non veg food",
        "nonveg",
        "nonveg food",
    }

    if text in vegetarian_values:
        return "vegetarian"
    if text in non_vegetarian_values:
        return "non-vegetarian"

    return text


def _food_type_score(requested_type, actual_type):
    requested = _normalize_food_type(requested_type)
    actual = _normalize_food_type(actual_type)

    if requested == "any":
        # Any food is acceptable; type itself should not dominate the score.
        return 100
    if actual == requested:
        return 100
    return 0


def _distance_score(distance_km, max_distance_km):
    try:
        distance = max(0.0, float(distance_km))
        maximum = max(0.1, float(max_distance_km))
    except Exception:
        return 0

    if distance > maximum:
        return 0

    # A listing at the receiver is 100; a listing at the edge of the
    # requested radius is 0. This gives shorter trips a consistently higher
    # score while still respecting the selected maximum distance.
    return round(max(0.0, 100.0 * (1.0 - (distance / maximum))))


def _quantity_score(food_quantity, needed_quantity):
    requested = _quantity_number(needed_quantity)
    available = _quantity_number(food_quantity)

    if requested is None or requested <= 0:
        return 70 if available is not None else 50

    if available is None:
        # The hard filter intentionally allows unparseable quantities so the
        # provider's text is not incorrectly discarded.
        return 50

    if available < requested:
        return 0

    ratio = available / requested

    # Enough food gets a strong score. Excess food is useful, but not
    # infinitely better than an exact quantity.
    if ratio <= 1.25:
        return 100
    if ratio <= 2:
        return 95
    if ratio <= 5:
        return 85
    return 75


def _parse_expiry(value):
    """Best-effort parser for common database date/time formats."""
    text = str(value or "").strip()
    if not text:
        return None

    # ISO values, including trailing Z.
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            pass

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def _urgency_score(available_until):
    """Score expiry urgency: sooner expiry means a higher rescue priority."""
    expiry = _parse_expiry(available_until)
    if expiry is None:
        # Missing/unknown expiry should not unfairly outrank known urgent food.
        return 60

    now = datetime.now(timezone.utc)
    hours_left = (expiry - now).total_seconds() / 3600.0

    if hours_left <= 0:
        return 0
    if hours_left <= 1:
        return 100
    if hours_left <= 3:
        return 95
    if hours_left <= 6:
        return 85
    if hours_left <= 12:
        return 75
    if hours_left <= 24:
        return 65
    if hours_left <= 48:
        return 55
    return 45


def _pickup_score(item):
    """Score practical pickup based on usable location data and distance."""
    distance = float(item.get("distance_km", 999))
    has_location = bool(str(item.get("location") or "").strip())
    has_coordinates = (
        item.get("latitude") is not None and item.get("longitude") is not None
    )

    if distance <= 1:
        base = 100
    elif distance <= 3:
        base = 95
    elif distance <= 5:
        base = 85
    elif distance <= 10:
        base = 70
    else:
        base = 50

    if has_location and has_coordinates:
        return base
    if has_coordinates:
        return max(0, base - 10)
    return max(0, base - 30)


def _calculate_match_score(item, food_type, max_distance_km, needed_quantity):
    type_score = _food_type_score(food_type, item.get("food_type"))
    distance_score = _distance_score(
        item.get("distance_km", 999),
        max_distance_km,
    )
    quantity_score = _quantity_score(
        item.get("quantity"),
        needed_quantity,
    )
    urgency_score = _urgency_score(item.get("available_until"))
    pickup_score = _pickup_score(item)

    weighted = (
        type_score * SCORE_WEIGHTS["food_type"] / 100
        + distance_score * SCORE_WEIGHTS["distance"] / 100
        + quantity_score * SCORE_WEIGHTS["quantity"] / 100
        + urgency_score * SCORE_WEIGHTS["urgency"] / 100
        + pickup_score * SCORE_WEIGHTS["pickup"] / 100
    )

    return round(weighted), {
        "food_type": type_score,
        "distance": distance_score,
        "quantity": quantity_score,
        "urgency": urgency_score,
        "pickup": pickup_score,
    }


def _hard_filter_candidates(receiver_id, food_type, max_distance_km, needed_quantity):
    location = database.get_receiver_location(receiver_id)
    if not location:
        return None, [], "Please share your location first."

    try:
        receiver_lat = float(location["latitude"])
        receiver_lon = float(location["longitude"])
    except Exception:
        return None, [], "Invalid receiver location."

    foods = database.get_available_food()
    candidates = []

    requested_type = _normalize_food_type(food_type)

    for raw_food in foods:
        food = _to_dict(raw_food)
        if not food:
            continue

        try:
            food_lat = food.get("latitude")
            food_lon = food.get("longitude")

            if food_lat is None or food_lon is None:
                continue

            distance = database.calculate_distance_km(
                receiver_lat,
                receiver_lon,
                float(food_lat),
                float(food_lon),
            )

            if distance > max_distance_km:
                continue

            actual_type = _normalize_food_type(food.get("food_type"))
            if requested_type != "any" and actual_type != requested_type:
                continue

            if not _quantity_compatible(food.get("quantity"), needed_quantity):
                continue

            candidates.append({
                "food_id": int(food["id"]),
                "food_name": str(food.get("food_name") or "Food"),
                "food_type": str(food.get("food_type") or "Other"),
                "quantity": str(food.get("quantity") or ""),
                "provider_name": str(food.get("provider_name") or "Provider"),
                "location": str(food.get("location") or ""),
                "available_until": str(food.get("available_until") or ""),
                "distance_km": round(distance, 2),
                "latitude": float(food_lat),
                "longitude": float(food_lon),
            })
        except Exception as exc:
            print("AI CANDIDATE ERROR:", exc)

    candidates.sort(key=lambda item: item["distance_km"])
    return location, candidates, None


def _deterministic_rank(candidates, food_type, max_distance_km, needed_quantity):
    """Calculate the authoritative Phase 3.2 score locally."""
    results = []

    for candidate in candidates:
        item = dict(candidate)
        score, breakdown = _calculate_match_score(
            item,
            food_type,
            max_distance_km,
            needed_quantity,
        )
        item["score"] = score
        item["score_breakdown"] = breakdown
        results.append(item)

    results.sort(
        key=lambda item: (
            item["score"],
            -float(item.get("distance_km", 999)),
        ),
        reverse=True,
    )
    return results


def _build_local_reason(item, food_type, needed_quantity):
    breakdown = item.get("score_breakdown", {})
    strongest = sorted(
        breakdown.items(),
        key=lambda pair: pair[1],
        reverse=True,
    )[:2]

    labels = {
        "food_type": "food preference",
        "distance": "short pickup distance",
        "quantity": "quantity suitability",
        "urgency": "availability urgency",
        "pickup": "pickup practicality",
    }

    reasons = [labels.get(key, key) for key, _ in strongest]
    if reasons:
        return "Strong match based on " + " and ".join(reasons) + "."
    return "Recommended based on the FoodLoop matching score."


def _add_local_reasons(matches, food_type, needed_quantity):
    for item in matches:
        if not str(item.get("reason") or "").strip():
            item["reason"] = _build_local_reason(
                item,
                food_type,
                needed_quantity,
            )
    return matches


def _extract_json(text):
    if not text:
        return None

    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    cleaned = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    start_positions = [
        p for p in (cleaned.find("{"), cleaned.find("[")) if p >= 0
    ]
    if not start_positions:
        return None

    start = min(start_positions)
    for end in range(len(cleaned), start, -1):
        chunk = cleaned[start:end].strip()
        try:
            return json.loads(chunk)
        except Exception:
            continue

    return None


def _apply_gemini_reasons(ai_data, matches):
    """Use Gemini only for explanations; never accept its numerical score."""
    if isinstance(ai_data, dict):
        raw_matches = ai_data.get("matches", [])
        recommendation = str(ai_data.get("recommendation") or "")[:500]
    elif isinstance(ai_data, list):
        raw_matches = ai_data
        recommendation = ""
    else:
        raw_matches = []
        recommendation = ""

    reasons_by_id = {}
    for raw in raw_matches:
        if not isinstance(raw, dict):
            continue
        try:
            food_id = int(raw.get("food_id"))
        except Exception:
            continue
        reason = str(raw.get("reason") or "").strip()
        if reason:
            reasons_by_id[food_id] = reason[:500]

    for item in matches:
        item["reason"] = reasons_by_id.get(
            item["food_id"],
            item.get("reason") or "Recommended by the FoodLoop matching agent.",
        )

    return matches, recommendation


def _gemini_explain(candidates, matches, location, food_type, max_distance_km, needed_quantity):
    """Ask Gemini for short explanations of already-scored real candidates."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or genai is None or types is None:
        return matches, ""

    # Only send the top candidates to keep the prompt small and focused.
    explanation_candidates = []
    by_id = {item["food_id"]: item for item in matches}
    for item in matches[:10]:
        explanation_candidates.append({
            "food_id": item["food_id"],
            "food_name": item["food_name"],
            "food_type": item["food_type"],
            "quantity": item["quantity"],
            "provider_name": item["provider_name"],
            "location": item["location"],
            "available_until": item["available_until"],
            "distance_km": item["distance_km"],
            "score": item["score"],
            "score_breakdown": item["score_breakdown"],
        })

    prompt = f"""
You are the explanation layer of the FoodLoop AI food redistribution agent.

The backend has ALREADY calculated the authoritative match scores below.
Do not change, recalculate, or question the scores.
Do not invent any facts.
Do not request food, reserve food, modify the database, or perform actions.
Only provide a short explanation for each real food listing.

Receiver preference: {food_type or 'Any Food'}
Maximum distance: {max_distance_km} km
Needed quantity: {needed_quantity or 'Not specified'}
Receiver location: {json.dumps(_to_dict(location), ensure_ascii=False)}

Already-scored real listings:
{json.dumps(explanation_candidates, ensure_ascii=False, indent=2)}

Return ONLY valid JSON:
{{
  "recommendation": "one short sentence about the best match",
  "matches": [
    {{
      "food_id": 123,
      "reason": "one short factual explanation based only on the supplied data"
    }}
  ]
}}
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

        ai_data = _extract_json(response.text)
        return _apply_gemini_reasons(ai_data, matches)
    except Exception as exc:
        print("GEMINI EXPLANATION ERROR:", exc)
        return matches, ""


def run_food_matching_agent(
    receiver_id: int,
    food_type: str = "Any Food",
    max_distance_km: float = 10,
    needed_quantity: str = "",
):
    """
    Phase 3.2 Intelligent Food Matching flow:
      1. Read receiver location.
      2. Read real available food from SQLite.
      3. Apply hard safety/business filters.
      4. Calculate an authoritative 100-point local match score.
      5. Optionally ask Gemini for explanations only.
      6. Return validated recommendations.

    The agent never creates a food request. The user must explicitly
    click Request Food, which calls the normal request workflow.
    """

    try:
        max_distance_km = float(max_distance_km)
    except Exception:
        max_distance_km = 10

    max_distance_km = max(0.1, min(max_distance_km, 50))

    location, candidates, error = _hard_filter_candidates(
        receiver_id=receiver_id,
        food_type=food_type,
        max_distance_km=max_distance_km,
        needed_quantity=needed_quantity,
    )

    if error:
        return {
            "success": False,
            "ai_enabled": False,
            "matches": [],
            "recommendation": "",
            "message": error,
        }

    if not candidates:
        return {
            "success": True,
            "ai_enabled": bool(genai and os.getenv("GEMINI_API_KEY")),
            "matches": [],
            "recommendation": "No available food matched your current filters.",
            "message": "No matching food found nearby.",
            "candidate_count": 0,
        }

    # Phase 3.2: deterministic scoring is authoritative.
    matches = _deterministic_rank(
        candidates,
        food_type,
        max_distance_km,
        needed_quantity,
    )
    matches = _add_local_reasons(matches, food_type, needed_quantity)

    # Gemini is optional and is now explanation-only.
    gemini_enabled = bool(genai and os.getenv("GEMINI_API_KEY", "").strip())
    recommendation = ""

    if gemini_enabled:
        matches, recommendation = _gemini_explain(
            candidates,
            matches,
            location,
            food_type,
            max_distance_km,
            needed_quantity,
        )

    if not recommendation and matches:
        recommendation = (
            f"Best match: {matches[0]['food_name']} "
            f"({matches[0]['score']}% match, "
            f"{matches[0]['distance_km']} km away)."
        )

    return {
        "success": True,
        "ai_enabled": gemini_enabled,
        "matches": matches,
        "recommendation": recommendation,
        "model": MODEL_NAME if gemini_enabled else None,
        "candidate_count": len(candidates),
        "scoring": {
            "food_type": 25,
            "distance": 25,
            "quantity": 20,
            "urgency": 20,
            "pickup": 10,
        },
    }


# ============================================================
# PHASE 3.3 - RECEIVER SELECTION AGENT
# ============================================================

RECEIVER_SCORE_WEIGHTS = {
    "distance": 50,
    "location_freshness": 20,
    "eligibility": 20,
    "pickup_practicality": 10,
}


def _receiver_freshness_score(updated_at):
    """Score how recently a receiver location was updated."""
    parsed = _parse_expiry(updated_at)
    if parsed is None:
        return 40

    hours_old = max(
        0.0,
        (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0,
    )

    if hours_old <= 1:
        return 100
    if hours_old <= 6:
        return 90
    if hours_old <= 24:
        return 80
    if hours_old <= 72:
        return 65
    return 45


def _receiver_distance_score(distance_km, max_distance_km):
    try:
        distance = max(0.0, float(distance_km))
        maximum = max(0.1, float(max_distance_km))
    except Exception:
        return 0

    if distance > maximum:
        return 0

    return round(max(0.0, 100.0 * (1.0 - distance / maximum)))


def _receiver_pickup_score(receiver):
    """Score practical pickup using real location/identity fields only."""
    has_name = bool(str(receiver.get("receiver_name") or "").strip())
    has_coordinates = (
        receiver.get("latitude") is not None
        and receiver.get("longitude") is not None
    )

    if has_name and has_coordinates:
        return 100
    if has_coordinates:
        return 80
    return 0


def _get_receiver_candidates(food_latitude, food_longitude, radius_km):
    """Read eligible receivers with a saved location from the database."""
    try:
        food_latitude = float(food_latitude)
        food_longitude = float(food_longitude)
        radius_km = max(0.1, min(float(radius_km), 50.0))
    except Exception:
        return []

    connection = database.get_connection()
    try:
        rows = connection.execute(
            """
            SELECT
                rl.id AS location_id,
                rl.receiver_id,
                rl.receiver_name,
                rl.latitude,
                rl.longitude,
                rl.updated_at,
                u.email,
                u.role
            FROM receiver_locations rl
            LEFT JOIN users u
                ON u.id = rl.receiver_id
            WHERE rl.receiver_id IS NOT NULL
            ORDER BY rl.id DESC
            """
        ).fetchall()

        seen_receiver_ids = set()
        candidates = []

        for raw in rows:
            receiver = _to_dict(raw)
            if not receiver:
                continue

            try:
                receiver_id = int(receiver.get("receiver_id"))
            except Exception:
                continue

            # Keep only actual receiver accounts.
            if str(receiver.get("role") or "").strip().lower() != "receiver":
                continue

            # receiver_locations can contain historical rows. Keep the newest
            # usable location per receiver.
            if receiver_id in seen_receiver_ids:
                continue

            try:
                lat = float(receiver.get("latitude"))
                lon = float(receiver.get("longitude"))
                distance = database.calculate_distance_km(
                    food_latitude,
                    food_longitude,
                    lat,
                    lon,
                )
            except Exception:
                continue

            if distance > radius_km:
                continue

            seen_receiver_ids.add(receiver_id)

            candidates.append({
                "receiver_id": receiver_id,
                "receiver_name": str(
                    receiver.get("receiver_name") or "Receiver"
                ),
                "email": str(receiver.get("email") or ""),
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(distance, 2),
                "updated_at": str(receiver.get("updated_at") or ""),
            })

        return candidates
    finally:
        connection.close()


def _score_receiver_candidates(candidates, max_distance_km):
    results = []

    for receiver in candidates:
        distance_score = _receiver_distance_score(
            receiver.get("distance_km", 999),
            max_distance_km,
        )
        freshness_score = _receiver_freshness_score(
            receiver.get("updated_at")
        )
        eligibility_score = 100
        pickup_score = _receiver_pickup_score(receiver)

        weighted = (
            distance_score * RECEIVER_SCORE_WEIGHTS["distance"] / 100
            + freshness_score * RECEIVER_SCORE_WEIGHTS["location_freshness"] / 100
            + eligibility_score * RECEIVER_SCORE_WEIGHTS["eligibility"] / 100
            + pickup_score * RECEIVER_SCORE_WEIGHTS["pickup_practicality"] / 100
        )

        item = dict(receiver)
        item["score"] = round(weighted)
        item["score_breakdown"] = {
            "distance": distance_score,
            "location_freshness": freshness_score,
            "eligibility": eligibility_score,
            "pickup_practicality": pickup_score,
        }
        results.append(item)

    results.sort(
        key=lambda item: (
            item["score"],
            -float(item.get("distance_km", 999)),
        ),
        reverse=True,
    )
    return results


def _receiver_local_reason(item):
    distance = item.get("distance_km", "?")
    score = item.get("score", 0)
    freshness = item.get("score_breakdown", {}).get("location_freshness", 0)

    return (
        f"Recommended as a strong receiver because it is {distance} km from "
        f"the food pickup point, has a valid receiver account, and has a "
        f"location freshness score of {freshness}/100. Overall match: {score}%."
    )


def _gemini_receiver_explain(food, matches):
    """Explain deterministic receiver ranking without changing the score."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or genai is None or types is None or not matches:
        return matches, ""

    receiver_data = []
    for item in matches[:10]:
        receiver_data.append({
            "receiver_id": item["receiver_id"],
            "receiver_name": item["receiver_name"],
            "distance_km": item["distance_km"],
            "score": item["score"],
            "score_breakdown": item["score_breakdown"],
            "location_updated_at": item.get("updated_at", ""),
        })

    safe_food = {
        "food_id": food.get("id"),
        "food_name": food.get("food_name"),
        "food_type": food.get("food_type"),
        "quantity": food.get("quantity"),
        "pickup_location": food.get("pickup_location") or food.get("location"),
        "available_until": food.get("available_until"),
    }

    prompt = f"""
You are the explanation layer of the FoodLoop AI Receiver Selection Agent.

The backend has already calculated the authoritative receiver scores.
Do not change scores. Do not invent receiver capacity, preferences,
organization details, phone numbers, or any other facts.
Do not contact receivers, create requests, reserve food, or modify the database.
Only explain why the real candidates are ranked highly.

Food:
{json.dumps(safe_food, ensure_ascii=False, indent=2)}

Real receiver candidates:
{json.dumps(receiver_data, ensure_ascii=False, indent=2)}

Return ONLY valid JSON:
{{
  "recommendation": "one short sentence naming the strongest receiver and why",
  "matches": [
    {{
      "receiver_id": 123,
      "reason": "one short factual explanation"
    }}
  ]
}}
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        data = _extract_json(response.text)

        reasons = {}
        if isinstance(data, dict):
            for raw in data.get("matches", []) or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    receiver_id = int(raw.get("receiver_id"))
                except Exception:
                    continue
                reason = str(raw.get("reason") or "").strip()
                if reason:
                    reasons[receiver_id] = reason[:500]

            recommendation = str(data.get("recommendation") or "").strip()
        else:
            recommendation = ""

        for item in matches:
            item["reason"] = reasons.get(
                item["receiver_id"],
                item.get("reason") or _receiver_local_reason(item),
            )

        return matches, recommendation
    except Exception as exc:
        print("GEMINI RECEIVER EXPLANATION ERROR:", exc)
        return matches, ""


def run_receiver_selection_agent(
    food_id: int,
    max_distance_km: float = 10,
):
    """
    Phase 3.3 Receiver Selection Agent.

    Observe -> validate food -> find eligible receivers -> calculate a
    deterministic receiver score -> optionally explain with Gemini.

    This agent is recommendation-only. It does NOT create a request,
    accept/reject a request, send notifications, or modify the database.
    """
    try:
        food_id = int(food_id)
    except Exception:
        return {
            "success": False,
            "matches": [],
            "message": "Invalid food ID.",
        }

    if food_id <= 0:
        return {
            "success": False,
            "matches": [],
            "message": "Invalid food ID.",
        }

    try:
        max_distance_km = float(max_distance_km)
    except Exception:
        max_distance_km = 10

    max_distance_km = max(0.1, min(max_distance_km, 50))

    food = database.get_food_by_id(food_id)
    if not food:
        return {
            "success": False,
            "matches": [],
            "message": "Food listing not found.",
        }

    food = _to_dict(food)
    status = str(food.get("status") or "available").strip().lower()
    if status != "available":
        return {
            "success": False,
            "matches": [],
            "message": "Only available food can be matched to receivers.",
        }

    if food.get("latitude") is None or food.get("longitude") is None:
        return {
            "success": False,
            "matches": [],
            "message": "This food listing does not have a valid pickup location.",
        }

    candidates = _get_receiver_candidates(
        food.get("latitude"),
        food.get("longitude"),
        max_distance_km,
    )

    if not candidates:
        return {
            "success": True,
            "ai_enabled": bool(genai and os.getenv("GEMINI_API_KEY", "").strip()),
            "matches": [],
            "recommendation": "No eligible receivers with saved locations were found within the selected radius.",
            "candidate_count": 0,
            "food": {
                "food_id": food_id,
                "food_name": food.get("food_name"),
            },
        }

    matches = _score_receiver_candidates(candidates, max_distance_km)

    for item in matches:
        item["reason"] = _receiver_local_reason(item)

    gemini_enabled = bool(genai and os.getenv("GEMINI_API_KEY", "").strip())
    recommendation = ""

    if gemini_enabled:
        matches, recommendation = _gemini_receiver_explain(food, matches)

    if not recommendation and matches:
        best = matches[0]
        recommendation = (
            f"Best receiver: {best['receiver_name']} "
            f"({best['score']}% match, {best['distance_km']} km away)."
        )

    return {
        "success": True,
        "ai_enabled": gemini_enabled,
        "matches": matches,
        "recommendation": recommendation,
        "model": MODEL_NAME if gemini_enabled else None,
        "candidate_count": len(candidates),
        "food": {
            "food_id": food_id,
            "food_name": food.get("food_name"),
            "food_type": food.get("food_type"),
            "quantity": food.get("quantity"),
            "pickup_location": food.get("pickup_location") or food.get("location"),
            "available_until": food.get("available_until"),
        },
        "scoring": {
            "distance": 50,
            "location_freshness": 20,
            "eligibility": 20,
            "pickup_practicality": 10,
        },
    }
