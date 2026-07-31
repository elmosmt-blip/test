# Dashboard

File:

```text
dashboard/app.py
```

Start:

```bash
./start-dashboard.sh
```

URL:

```text
http://127.0.0.1:8800
```

Endpoints:

```text
GET  /status
GET  /briefs
GET  /article
GET  /drafts
POST /run/{agent_id}
POST /run/all/pipeline
POST /drafts/{id}/approve
DELETE /drafts/{id}
```

## Safe mode

When:

```env
ALLOW_DB_WRITES=0
```

Dashboard blocks:

- run agent #6;
- run agent #7;
- approve;
- delete.

Read-only status and drafts still work.
