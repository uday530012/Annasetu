# ============================================================
# FOODLOOP AI - PHASE 3.6 MULTI-AGENT WORKFLOW ORCHESTRATOR
# ============================================================
"""
Phase 3.6: Coordinate the existing FoodLoop agents without allowing
agents to bypass the normal request/security workflow.

Workflow:
  1. Observe/validate provider-owned food.
  2. Receiver Selection Agent ranks eligible receivers.
  3. Expiry/Rescue Agent evaluates urgency without inventing expiry.
  4. Notification Agent sends normal AI match notifications, OR the
     Expiry/Rescue Agent sends urgent rescue notifications.
  5. Optional Food Matching Agent verifies the top receiver's food
     compatibility context; it never creates a request.

No agent accepts/rejects/collects a request automatically.
"""

from datetime import datetime, timezone

import database

try:
    from ai_agent import run_receiver_selection_agent, run_food_matching_agent
except Exception:
    run_receiver_selection_agent = None
    run_food_matching_agent = None

try:
    from notification_agent import run_notification_agent
except Exception:
    run_notification_agent = None

try:
    from expiry_rescue_agent import analyze_food_expiry
except Exception:
    analyze_food_expiry = None


def _parse_datetime(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _workflow_urgency(food):
    parsed = _parse_datetime(food.get("available_until"))
    if parsed is None:
        return {"level": "unknown", "urgency_score": 0, "should_rescue": False, "minutes_remaining": None}
    minutes = (parsed - datetime.now(timezone.utc)).total_seconds() / 60.0
    if minutes <= 0:
        return {"level": "expired", "urgency_score": 100, "should_rescue": False, "minutes_remaining": round(minutes, 2)}
    if minutes <= 60:
        return {"level": "critical", "urgency_score": 100, "should_rescue": True, "minutes_remaining": round(minutes, 2)}
    if minutes <= 180:
        return {"level": "urgent", "urgency_score": 80, "should_rescue": True, "minutes_remaining": round(minutes, 2)}
    if minutes <= 360:
        return {"level": "high", "urgency_score": 60, "should_rescue": True, "minutes_remaining": round(minutes, 2)}
    if minutes <= 1440:
        return {"level": "rescue", "urgency_score": 40, "should_rescue": True, "minutes_remaining": round(minutes, 2)}
    return {"level": "normal", "urgency_score": 10, "should_rescue": False, "minutes_remaining": round(minutes, 2)}


def _food_owned_by_provider(food, provider_id):
    owner = food.get("provider_id")
    return owner is not None and str(owner) == str(provider_id)


def run_multi_agent_workflow(
    food_id: int,
    provider_id: int,
    max_distance_km: float = 10,
    max_notifications: int = 3,
):
    """Run the coordinated Phase 3.6 workflow for one provider-owned food listing."""
    try:
        food_id = int(food_id)
        provider_id = int(provider_id)
    except Exception:
        return {"success": False, "message": "Invalid food or provider ID."}
    if food_id <= 0 or provider_id <= 0:
        return {"success": False, "message": "Invalid food or provider ID."}

    try:
        max_distance_km = max(0.1, min(float(max_distance_km), 50))
    except Exception:
        max_distance_km = 10
    try:
        max_notifications = max(1, min(int(max_notifications), 5))
    except Exception:
        max_notifications = 3

    raw_food = database.get_food_by_id(food_id)
    if not raw_food:
        return {"success": False, "message": "Food listing not found."}
    food = dict(raw_food)

    if not _food_owned_by_provider(food, provider_id):
        return {"success": False, "message": "You can only run the workflow for your own food listings."}

    status = str(food.get("status") or "available").strip().lower()
    if status != "available":
        return {"success": False, "message": f"Food is not currently available (status: {status}).", "food_id": food_id}

    # Agent 1: Receiver Selection
    if run_receiver_selection_agent is None:
        return {"success": False, "message": "Receiver Selection Agent is unavailable."}
    try:
        selection = run_receiver_selection_agent(food_id=food_id, max_distance_km=max_distance_km)
    except Exception as exc:
        return {"success": False, "message": f"Receiver Selection Agent failed: {exc}"}
    if not selection.get("success"):
        return {"success": False, "message": selection.get("message") or "Receiver selection failed.", "selection": selection}

    matches = selection.get("matches") or []
    best_receiver = matches[0] if matches else None

    # Agent 2: Expiry/Rescue analysis, but do not send a second notification yet.
    urgency = _workflow_urgency(food)
    expiry_result = None
    if analyze_food_expiry is not None:
        try:
            expiry_result = analyze_food_expiry(
                food_id=food_id,
                max_distance_km=max_distance_km,
                notify=False,
            )
            if isinstance(expiry_result, dict) and isinstance(expiry_result.get("urgency"), dict):
                urgency = dict(expiry_result["urgency"], minutes_remaining=expiry_result.get("minutes_remaining"))
        except Exception as exc:
            expiry_result = {"success": False, "message": f"Expiry analysis failed: {exc}"}

    if urgency.get("level") == "expired":
        return {
            "success": True,
            "workflow_completed": True,
            "message": "Workflow stopped because the food has expired. No notification was sent.",
            "food_id": food_id,
            "food": {"food_name": food.get("food_name"), "quantity": food.get("quantity"), "available_until": food.get("available_until")},
            "agents": {"receiver_selection": True, "expiry_rescue": True, "notification": False, "food_matching": False},
            "urgency": urgency,
            "matches": matches,
            "best_receiver": best_receiver,
            "notified": [],
        }

    # Agent 3: Optional food-matching verification for the top receiver.
    matching_result = None
    if best_receiver and run_food_matching_agent is not None:
        try:
            matching_result = run_food_matching_agent(
                receiver_id=int(best_receiver.get("receiver_id")),
                food_type=str(food.get("food_type") or "Any Food"),
                max_distance_km=max_distance_km,
                needed_quantity=str(food.get("quantity") or ""),
            )
        except Exception as exc:
            matching_result = {"success": False, "message": f"Food Matching Agent verification failed: {exc}"}

    # Agent 4: Notification / Rescue action. Urgent food goes through the
    # Rescue Agent so its wider emergency radius and urgency message are used.
    notification_result = None
    action_agent = "none"
    if urgency.get("should_rescue") and analyze_food_expiry is not None:
        action_agent = "expiry_rescue"
        try:
            notification_result = analyze_food_expiry(
                food_id=food_id,
                max_distance_km=max_distance_km,
                max_notifications=max_notifications,
                notify=True,
            )
        except Exception as exc:
            notification_result = {"success": False, "message": f"Rescue notification failed: {exc}", "notified": []}
    elif run_notification_agent is not None:
        action_agent = "notification"
        try:
            notification_result = run_notification_agent(
                food_id=food_id,
                max_distance_km=max_distance_km,
                max_notifications=max_notifications,
            )
        except Exception as exc:
            notification_result = {"success": False, "message": f"Notification Agent failed: {exc}", "notified": []}
    else:
        notification_result = {"success": False, "message": "Notification Agent is unavailable.", "notified": []}

    notified = (notification_result or {}).get("notified") or []
    notifications_created = len(notified)

    if action_agent == "expiry_rescue":
        action_message = "Expiry/Rescue Agent handled the receiver notification because the food is within the rescue window."
    else:
        action_message = "Notification Agent handled the receiver notification because the food is not currently urgent."

    return {
        "success": True,
        "workflow_completed": True,
        "message": f"Multi-agent workflow completed. {action_message}",
        "food_id": food_id,
        "food": {
            "food_name": food.get("food_name"),
            "food_type": food.get("food_type"),
            "quantity": food.get("quantity"),
            "pickup_location": food.get("pickup_location") or food.get("location"),
            "available_until": food.get("available_until"),
        },
        "urgency": urgency,
        "best_receiver": best_receiver,
        "matches": matches[:max_notifications],
        "notified": notified,
        "notifications_created": notifications_created,
        "action_agent": action_agent,
        "agents": {
            "receiver_selection": True,
            "expiry_rescue": analyze_food_expiry is not None,
            "notification": run_notification_agent is not None,
            "food_matching": run_food_matching_agent is not None,
        },
        "agent_results": {
            "receiver_selection": selection,
            "expiry_rescue_analysis": expiry_result,
            "food_matching_verification": matching_result,
            "notification_or_rescue": notification_result,
        },
    }


def run_provider_workflow_scan(provider_id: int, max_distance_km: float = 10, max_notifications: int = 3):
    """Run the workflow for every currently available food owned by a provider."""
    try:
        provider_id = int(provider_id)
    except Exception:
        return {"success": False, "message": "Invalid provider ID."}
    if provider_id <= 0:
        return {"success": False, "message": "Invalid provider ID."}

    foods = database.get_available_food()
    owned = [dict(food) for food in foods if _food_owned_by_provider(dict(food), provider_id)]
    results = []
    for food in owned:
        try:
            results.append(run_multi_agent_workflow(
                food_id=int(food["id"]),
                provider_id=provider_id,
                max_distance_km=max_distance_km,
                max_notifications=max_notifications,
            ))
        except Exception as exc:
            results.append({"success": False, "food_id": food.get("id"), "message": str(exc)})

    completed = sum(1 for item in results if item.get("success"))
    notifications = sum(int(item.get("notifications_created", 0) or 0) for item in results)
    urgent = sum(1 for item in results if (item.get("urgency") or {}).get("should_rescue"))
    return {
        "success": True,
        "message": f"Multi-agent workflow scan completed for {len(owned)} available food listing(s).",
        "food_count": len(owned),
        "completed_count": completed,
        "urgent_count": urgent,
        "notification_count": notifications,
        "foods": results,
    }
