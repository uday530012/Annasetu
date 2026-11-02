FOODLOOP AI - PHASE 4.5: IMPACT REPORTING & EXPORT

This phase builds on the working Phase 4.4 Provider & Receiver Performance Analytics.

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
- provider.html
- make_admin.py

NO DATABASE SCHEMA CHANGES.

NEW FEATURES:
- Unified /api/admin/report read-only impact report
- Phase 4.5 Impact Reporting & Export section
- Date-range-aware report generation
- Impact KPI summary
- Network summary
- Top provider report
- Top receiver report
- Summary CSV export
- Provider/receiver performance CSV export
- Monthly trend CSV export
- Existing Print Report support
- Admin-only authorization on all report endpoints

NEW ENDPOINTS:
GET /api/admin/report
GET /api/admin/export/summary.csv
GET /api/admin/export/performance.csv
GET /api/admin/export/trends.csv

INSTALL:
1. Backup your project.
2. Replace the three files listed above.
3. Do NOT replace database.py or foodloop.db.
4. Start:
   uvicorn main:app --reload
5. Open:
   http://127.0.0.1:8000/admin
6. Log in with your admin account.
7. Scroll to "Phase 4.5 - Impact Reporting & Export".
8. Click "Generate Impact Report".
9. Test the three CSV buttons.

SECURITY:
- All Phase 4.5 endpoints require the existing admin authorization.
- The reporting layer is read-only.
- No food/request/notification mutations are performed.
