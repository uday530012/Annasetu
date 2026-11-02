FOODLOOP AI - PHASE 4.6: DATE-RANGE REPORTS & PERIOD COMPARISON

Builds on Phase 4.5 Impact Reporting & Export.

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
- make_admin.py

The package includes the existing login.html so the login page is preserved.

NO DATABASE SCHEMA CHANGES.

NEW PHASE 4.6 FEATURES:
- Quick date presets: Today, Yesterday, Last 7 Days, Last 30 Days, This Month, Last Month, This Year, All Time.
- Custom start/end date range.
- Strict range-based KPI calculation for food, rescue, requests, users and notifications.
- Previous-period comparison using an equal-length preceding period.
- Percentage change for key metrics.
- Daily activity report for ranges up to 366 days.
- Date-range provider performance.
- Date-range receiver performance.
- Date-range CSV export.
- Admin-only read-only API endpoints.
- No food/request/notification mutations.

NEW ENDPOINTS:
GET /api/admin/date-range-report
GET /api/admin/export/date-range.csv

EXAMPLE:
/api/admin/date-range-report?preset=last_30_days&compare=true

INSTALL:
1. Backup your working FoodLoop AI project.
2. Replace main.py, admin_dashboard.py and templates/admin.html.
3. Keep your existing database.py and foodloop.db.
4. Keep your existing .env and Phase 3 agent files.
5. Keep templates/login.html. The included login.html is the same login template used by the working Phase 4 package.
6. Start:
   uvicorn main:app --reload
7. Open:
   http://127.0.0.1:8000/admin
8. Login using the existing admin account.
9. Scroll to "Phase 4.6 - Date-Range Reports & Period Comparison".
10. Select a quick range or Custom Range and click Generate Date-Range Report.
11. Test Date-Range CSV.

SECURITY:
- Both Phase 4.6 endpoints require the existing admin authorization.
- Reporting is read-only.
- No database schema changes are made.
- Existing operational security and Phase 3 agent functionality are preserved.
