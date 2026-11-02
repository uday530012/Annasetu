FOODLOOP AI - PHASE 4.7
ADMIN CONTROL CENTER & IMPACT INTELLIGENCE

BUILDS ON PHASE 4.6

REPLACE:
- main.py
- admin_dashboard.py
- templates/admin.html

KEEP:
- database.py
- foodloop.db
- .env
- ai_agent.py
- notification_agent.py
- expiry_rescue_agent.py
- workflow_agent.py
- agent_dashboard.py
- provider.html
- receiver.html
- make_admin.py (included here for convenience)

NO DATABASE SCHEMA CHANGES ARE REQUIRED.

NEW FEATURES:
1. Admin Control Center
2. System health summary
3. Read-only data-quality audit
4. Impact Index
5. Food rescued / quantity rescued / estimated meals rescued
6. Rescue and collection rates
7. Provider and receiver totals
8. AI notification count
9. Food-type distribution
10. Read-only searchable user directory
11. Impact CSV export
12. Users CSV export

NEW ADMIN ENDPOINTS:
GET /api/admin/control-center
GET /api/admin/data-quality
GET /api/admin/users
GET /api/admin/export/impact.csv
GET /api/admin/export/users.csv

SECURITY:
- All new endpoints require the existing admin authorization.
- Reporting and directory endpoints are read-only.
- No food, request, notification, user, or location records are modified by Phase 4.7.
- Existing Phase 2 security middleware remains in main.py.

INSTALL:
1. Backup your current working project.
2. Replace main.py with the included main.py.
3. Replace admin_dashboard.py with the included admin_dashboard.py.
4. Replace templates/admin.html with the included admin.html.
5. Keep your existing database.py and foodloop.db.
6. Keep your existing .env and Phase 3 agent files.
7. Start:
   uvicorn main:app --reload
8. Login with your existing admin-enabled account.
9. Open:
   http://127.0.0.1:8000/admin

IMPORTANT:
The Impact Index is an operational dashboard indicator, not a scientific environmental measurement.
The estimated meals rescued value is based on the numeric quantity stored in food listings.
