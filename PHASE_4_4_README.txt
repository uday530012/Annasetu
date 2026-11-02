FoodLoop AI - Phase 4.4 Provider & Receiver Performance Analytics

Replace main.py, admin_dashboard.py, and templates/admin.html with the files in this package.
Keep your existing database.py, foodloop.db, .env, and Phase 3 agent files. Do NOT delete the database.

Run:
  uvicorn main:app --reload

Open:
  http://127.0.0.1:8000/admin

Features:
- Provider performance: food listings, available/rescued/expired food, requests, collected requests, rescue rate, request conversion, performance score.
- Receiver performance: requests, accepted/rejected/collected, success rate, active receiver location, performance score.
- Date-range aware analytics using existing admin date filters.
- Admin-only read-only endpoint: /api/admin/performance
- No database schema changes and no mutation of operational data.

Provider score = 60% rescue rate + 40% request conversion.
Receiver score = 70% collection success + 30% acceptance rate.
Scores are descriptive analytics, not permissions or automated decisions.
