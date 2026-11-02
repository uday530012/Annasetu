from database import get_available_food, calculate_distance_km


def calculate_food_match_score(
    food,
    receiver_latitude,
    receiver_longitude,
    preferred_food_type=None
):
    """
    Calculate a matching score for one food item.

    Higher score = better match.
    """

    score = 0

    # -----------------------------------------
    # 1. DISTANCE
    # -----------------------------------------

    food_latitude = food["latitude"]
    food_longitude = food["longitude"]

    distance = None

    if food_latitude is not None and food_longitude is not None:

        distance = calculate_distance_km(
            receiver_latitude,
            receiver_longitude,
            food_latitude,
            food_longitude
        )

        if distance <= 1:
            score += 40

        elif distance <= 3:
            score += 35

        elif distance <= 5:
            score += 30

        elif distance <= 10:
            score += 20

        elif distance <= 20:
            score += 10

    # -----------------------------------------
    # 2. FOOD TYPE
    # -----------------------------------------

    food_type = str(food["food_type"] or "").strip()

    if preferred_food_type:

        if food_type.lower() == preferred_food_type.lower():
            score += 30

        elif food_type.lower() == "other":
            score += 10

    else:
        score += 15

    # -----------------------------------------
    # 3. QUANTITY
    # -----------------------------------------

    try:
        quantity = int(food["quantity"])

        if quantity >= 20:
            score += 20

        elif quantity >= 10:
            score += 15

        elif quantity >= 5:
            score += 10

        else:
            score += 5

    except (ValueError, TypeError):
        pass

    # -----------------------------------------
    # 4. AVAILABILITY
    # -----------------------------------------

    status = str(food["status"] or "").lower()

    if status == "available":
        score += 10

    # -----------------------------------------
    # RETURN RESULT
    # -----------------------------------------

    return {
        "food_id": food["id"],
        "food_name": food["food_name"],
        "food_type": food["food_type"],
        "quantity": food["quantity"],
        "provider_name": food["provider_name"],
        "location": food["location"],
        "pickup_location": food["pickup_location"],
        "contact": food["contact"],
        "latitude": food["latitude"],
        "longitude": food["longitude"],
        "distance_km": round(distance, 2) if distance is not None else None,
        "match_score": score
    }


def find_best_food_matches(
    receiver_latitude,
    receiver_longitude,
    preferred_food_type=None,
    limit=5
):
    """
    Find and rank the best available food
    for the receiver.
    """

    foods = get_available_food()

    matches = []

    for food in foods:

        # Ignore food without location
        if food["latitude"] is None:
            continue

        if food["longitude"] is None:
            continue

        match = calculate_food_match_score(
            food,
            receiver_latitude,
            receiver_longitude,
            preferred_food_type
        )

        matches.append(match)

    # Highest score first
    matches.sort(
        key=lambda item: item["match_score"],
        reverse=True
    )

    return matches[:limit]