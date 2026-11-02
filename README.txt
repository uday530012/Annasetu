FOODLOOP AI - PHASE 4.7 COMPLETE FIX

FIX INCLUDED:
1. Corrected /post-food database.add_food() call using named arguments.
2. This fixes: NOT NULL constraint failed: food.available_until.
3. Included missing add_food.html, food_requests.html, and available_foods.html templates.
4. Included the Phase 4.7 admin dashboard files.

DO NOT replace database.py or foodloop.db. Keep your existing database and Phase 3 agent files.

INSTALL:
- Replace your main.py with this package's main.py.
- Copy all files from templates/ into your existing templates/ folder.
- Keep your existing database.py, foodloop.db, ai_agent.py, notification_agent.py, expiry_rescue_agent.py, workflow_agent.py, and agent_dashboard.py.
- Restart: uvicorn main:app --reload

The important fix is that available_until is now passed explicitly as available_until=available_until instead of being accidentally passed as the location argument.
