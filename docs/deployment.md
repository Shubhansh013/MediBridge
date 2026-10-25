# MediBridge Deployment Guide

**Version:** 1.0  
**Date:** 2026-10-24  
**Status:** Development Environment Documented, Production Pending

---

## 1. Overview

This guide covers deploying MediBridge from local development to production. The current implementation runs locally with SQLite; production deployment requires migration to PostgreSQL and proper infrastructure.

**Deployment Stages:**
1. **Local Development** (Current) — SQLite, Telegram polling, local Streamlit
2. **Staging** (Planned) — PostgreSQL, Telegram webhook, cloud-hosted Streamlit
3. **Production** (Future) — Scalable infrastructure, monitoring, backups

---

## 2. Prerequisites

### 2.1 System Requirements

**Minimum:**
- Python 3.10 or later
- 2 GB RAM
- 1 GB disk space (for database and logs)
- Internet connection (for Telegram API)

**Recommended (Production):**
- Python 3.11+
- 4 GB RAM
- 10 GB disk space
- PostgreSQL 14+
- Redis 7+ (for caching and task queue)
- Nginx (reverse proxy)
- Ubuntu 22.04 LTS or similar

### 2.2 Software Dependencies

```bash
# Core dependencies (from requirements.txt)
python-telegram-bot>=20.0
streamlit>=1.30.0
pandas>=2.0.0
pydantic>=2.7.0,<3.0.0
sqlalchemy>=2.0.30,<3.0.0
python-dotenv>=1.0.0,<2.0.0
pytest>=8.0.0,<9.0.0

# Production additions
psycopg2-binary>=2.9.9  # PostgreSQL adapter
redis>=5.0.0            # Caching
celery>=5.3.0           # Background tasks
gunicorn>=21.2.0        # WSGI server
uvicorn>=0.25.0         # ASGI server (if FastAPI)
```

---

## 3. Local Development Setup

### 3.1 Clone Repository

```bash
git clone https://github.com/your-org/MediBridge.git
cd MediBridge
```

### 3.2 Create Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3.3 Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.4 Configure Environment Variables

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your credentials
nano .env  # or use your preferred editor
```

**Required `.env` contents:**
```bash
# Telegram Bot Token (from @BotFather)
TELEGRAM_BOT_TOKEN=8803435363:AAEO79TaBl3Irs4P27aBAD1eKs65m79y5tI

# SQLite Database (local development)
DATABASE_URL=sqlite:///./data/medibridge.db

# Optional: LLM parser fallback (not active in current implementation)
GEMINI_API_KEY=your_gemini_key_here
GROQ_API_KEY=your_groq_key_here
```

### 3.5 Initialize Database

```bash
# Create data directory
mkdir -p data

# Initialize database with seed data
python -c "
from medibridge_db.database import engine, Base
from medibridge_db.seed import seed_all
from sqlalchemy.orm import Session

Base.metadata.create_all(engine)

with Session(engine) as session:
    seed_all(session)
    session.commit()
    print('Database seeded successfully')
"
```

**Verify seed data:**
```bash
sqlite3 data/medibridge.db "SELECT COUNT(*) FROM pharmacies; SELECT COUNT(*) FROM medicines;"
# Expected: 12 pharmacies, 50+ medicines
```

### 3.6 Run Tests

```bash
# Run all tests
pytest -v

# Run specific test file
pytest tests/test_ai_parser.py -v

# Run with coverage
pytest --cov=medibridge_db --cov=src/medibridge tests/
```

**Expected:** 100+ tests passing

### 3.7 Launch Applications

**Terminal 1: Telegram Bot**
```bash
python bot.py
```

**Terminal 2: Streamlit Dashboard**
```bash
streamlit run medibridge_dashboard.py
```

**Verify:**
- Bot responds to `/start` in Telegram
- Dashboard opens at `http://localhost:8501`
- Search for "Dolo 650" works in bot (patient mode)
- Send "Dolo 650 khatam" works in bot (chemist mode)

---

## 4. Production Deployment Architecture

```
                        ┌─────────────┐
                        │   Internet  │
                        └──────┬──────┘
                               │
                    ┌──────────┴──────────┐
                    │    Nginx (443)      │ ← SSL/TLS
                    │   Reverse Proxy     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              │                                 │
    ┌─────────▼────────┐           ┌───────────▼──────────┐
    │  FastAPI/Flask   │           │    Streamlit         │
    │  REST API        │           │    Dashboard         │
    │  (port 8000)     │           │    (port 8501)       │
    └─────────┬────────┘           └───────────┬──────────┘
              │                                 │
              └────────────────┬────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Application Layer  │
                    │  (services/)        │
                    └──────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              │                                 │
    ┌─────────▼────────┐           ┌───────────▼──────────┐
    │   PostgreSQL     │           │      Redis           │
    │   (port 5432)    │           │   (port 6379)        │
    │   (Primary DB)   │           │   (Cache/Queue)      │
    └──────────────────┘           └──────────────────────┘
```

---

## 5. Production Database Migration (SQLite → PostgreSQL)

### 5.1 Install PostgreSQL

**Ubuntu:**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**Create Database:**
```bash
sudo -u postgres psql

CREATE DATABASE medibridge;
CREATE USER medibridge_user WITH ENCRYPTED PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE medibridge TO medibridge_user;
\q
```

### 5.2 Update Environment Variables

```bash
# .env (production)
DATABASE_URL=postgresql://medibridge_user:your_secure_password@localhost:5432/medibridge
```

### 5.3 Migrate Data (Optional)

If migrating existing SQLite data:

```bash
# Export from SQLite
sqlite3 data/medibridge.db .dump > medibridge_sqlite_dump.sql

# Convert to PostgreSQL format (manual adjustments needed)
# - Replace AUTOINCREMENT with SERIAL
# - Fix SQLite-specific syntax

# Import to PostgreSQL
psql -U medibridge_user -d medibridge -f medibridge_postgres_dump.sql
```

**Alternative:** Re-seed database using `seed.py` (recommended for clean start)

```python
from medibridge_db.database import engine, Base
from medibridge_db.seed import seed_all
from sqlalchemy.orm import Session

# Update config.py to use PostgreSQL URL
Base.metadata.create_all(engine)

with Session(engine) as session:
    seed_all(session)
    session.commit()
```

---

## 6. Production Telegram Bot Deployment

### 6.1 Webhook vs Polling

**Current (Development):** Polling — Bot actively requests updates from Telegram

**Production:** Webhook — Telegram sends updates to your server

**Why Webhook:**
- Lower latency
- No constant polling overhead
- Scales better

### 6.2 Set Up Telegram Webhook

**Prerequisites:**
- Domain with SSL certificate (Let's Encrypt)
- Public-facing HTTPS endpoint

**Set webhook:**
```python
import os
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = "https://yourdomain.com/telegram/webhook"

response = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    json={"url": WEBHOOK_URL}
)

print(response.json())
# Expected: {"ok": true, "result": true, "description": "Webhook was set"}
```

**Update bot code:**
```python
# bot_webhook.py
from flask import Flask, request
from telegram import Update
import asyncio

app = Flask(__name__)

@app.route('/telegram/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.run(handle_update(update))
    return "OK"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
```

### 6.3 Run with Gunicorn

```bash
gunicorn -w 4 -b 0.0.0.0:8000 bot_webhook:app
```

---

## 7. Streamlit Dashboard Deployment

### 7.1 Run Streamlit in Production Mode

```bash
# streamlit_config.toml
[server]
port = 8501
enableCORS = false
enableXsrfProtection = true

[browser]
gatherUsageStats = false

[theme]
primaryColor = "#0f766e"
backgroundColor = "#f3f7f6"
```

**Run:**
```bash
streamlit run medibridge_dashboard.py --server.port 8501 --server.address 0.0.0.0
```

### 7.2 Nginx Reverse Proxy

```nginx
# /etc/nginx/sites-available/medibridge

server {
    listen 80;
    server_name dashboard.medibridge.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name dashboard.medibridge.example.com;

    ssl_certificate /etc/letsencrypt/live/dashboard.medibridge.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dashboard.medibridge.example.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

**Enable and restart:**
```bash
sudo ln -s /etc/nginx/sites-available/medibridge /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## 8. Systemd Service Configuration

### 8.1 Telegram Bot Service

```ini
# /etc/systemd/system/medibridge-bot.service

[Unit]
Description=MediBridge Telegram Bot
After=network.target postgresql.service

[Service]
Type=simple
User=medibridge
WorkingDirectory=/opt/medibridge
Environment="PATH=/opt/medibridge/.venv/bin"
ExecStart=/opt/medibridge/.venv/bin/gunicorn -w 4 -b 0.0.0.0:8000 bot_webhook:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 8.2 Streamlit Dashboard Service

```ini
# /etc/systemd/system/medibridge-dashboard.service

[Unit]
Description=MediBridge Streamlit Dashboard
After=network.target postgresql.service

[Service]
Type=simple
User=medibridge
WorkingDirectory=/opt/medibridge
Environment="PATH=/opt/medibridge/.venv/bin"
ExecStart=/opt/medibridge/.venv/bin/streamlit run medibridge_dashboard.py --server.port 8501 --server.address 0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable medibridge-bot
sudo systemctl enable medibridge-dashboard
sudo systemctl start medibridge-bot
sudo systemctl start medibridge-dashboard

# Check status
sudo systemctl status medibridge-bot
sudo systemctl status medibridge-dashboard
```

---

## 9. Background Tasks with Celery

### 9.1 Install Redis

```bash
sudo apt install redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

### 9.2 Celery Worker Configuration

```python
# celery_worker.py
from celery import Celery
from medibridge_db.database import get_session
from medibridge_db.operations import expire_overdue_proposals
from datetime import datetime, timezone

app = Celery('medibridge', broker='redis://localhost:6379/0')

@app.task
def expire_proposals_task():
    """Cron job: Mark overdue proposals as EXPIRED"""
    with get_session() as session:
        count = expire_overdue_proposals(session, cutoff=datetime.now(timezone.utc))
        session.commit()
        return f"Expired {count} proposals"

# Schedule
app.conf.beat_schedule = {
    'expire-proposals-every-hour': {
        'task': 'celery_worker.expire_proposals_task',
        'schedule': 3600.0,  # Every hour
    },
}
```

**Run worker:**
```bash
celery -A celery_worker worker --loglevel=info

# Run beat scheduler (in separate terminal)
celery -A celery_worker beat --loglevel=info
```

---

## 10. Monitoring and Logging

### 10.1 Application Logging

```python
# Configure logging in bot.py and services
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/medibridge/app.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('medibridge')
```

### 10.2 Log Rotation

```bash
# /etc/logrotate.d/medibridge

/var/log/medibridge/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0640 medibridge medibridge
    sharedscripts
    postrotate
        systemctl reload medibridge-bot
        systemctl reload medibridge-dashboard
    endscript
}
```

### 10.3 Health Check Endpoints

```python
# Add to bot_webhook.py
@app.route('/health', methods=['GET'])
def health_check():
    try:
        # Check database connection
        with get_session() as session:
            session.execute("SELECT 1")
        return {"status": "healthy", "database": "ok"}, 200
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}, 500
```

**Monitor with cron:**
```bash
# /etc/cron.d/medibridge-health
*/5 * * * * medibridge curl -f http://localhost:8000/health || systemctl restart medibridge-bot
```

---

## 11. Backup and Recovery

### 11.1 PostgreSQL Backup

**Daily automated backup:**
```bash
#!/bin/bash
# /opt/medibridge/scripts/backup.sh

BACKUP_DIR="/var/backups/medibridge"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/medibridge_$DATE.sql.gz"

mkdir -p $BACKUP_DIR

pg_dump -U medibridge_user medibridge | gzip > $BACKUP_FILE

# Keep only last 30 days
find $BACKUP_DIR -name "medibridge_*.sql.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_FILE"
```

**Add to crontab:**
```bash
# Run daily at 2 AM
0 2 * * * /opt/medibridge/scripts/backup.sh >> /var/log/medibridge/backup.log 2>&1
```

### 11.2 Recovery

```bash
# Restore from backup
gunzip < medibridge_20261024_020000.sql.gz | psql -U medibridge_user medibridge
```

---

## 12. Security Hardening

### 12.1 Environment Variables

**Never commit `.env` to Git:**
```bash
# .gitignore
.env
.env.local
.env.production
```

**Use secrets management in production:**
- AWS Secrets Manager
- HashiCorp Vault
- Azure Key Vault

### 12.2 Database Security

```sql
-- Restrict PostgreSQL access
-- /etc/postgresql/14/main/pg_hba.conf

# Local connections only
local   medibridge   medibridge_user   md5
host    medibridge   medibridge_user   127.0.0.1/32   md5
```

**Firewall:**
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 5432/tcp  # Block external PostgreSQL access
sudo ufw enable
```

### 12.3 API Rate Limiting

**Install flask-limiter:**
```bash
pip install flask-limiter
```

**Add to bot webhook:**
```python
from flask_limiter import Limiter

limiter = Limiter(
    app,
    key_func=lambda: request.remote_addr,
    default_limits=["200 per day", "50 per hour"]
)

@app.route('/telegram/webhook', methods=['POST'])
@limiter.limit("10 per minute")
def webhook():
    # ...
```

---

## 13. SSL/TLS Certificate (Let's Encrypt)

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d dashboard.medibridge.example.com

# Auto-renewal (cron already set by certbot)
sudo certbot renew --dry-run
```

---

## 14. Performance Optimization

### 14.1 PostgreSQL Tuning

```sql
-- /etc/postgresql/14/main/postgresql.conf

shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 64MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 2621kB
min_wal_size = 1GB
max_wal_size = 4GB
```

**Restart PostgreSQL:**
```bash
sudo systemctl restart postgresql
```

### 14.2 Redis Caching

```python
# Cache medicine search results
import redis
import json

redis_client = redis.Redis(host='localhost', port=6379, db=0)

def search_medicine_cached(query: str, limit: int):
    cache_key = f"search:{query}:{limit}"
    cached = redis_client.get(cache_key)
    
    if cached:
        return json.loads(cached)
    
    results = adapter.search_medicines(query, limit)
    redis_client.setex(cache_key, 3600, json.dumps(results))  # Cache 1 hour
    return results
```

---

## 15. Deployment Checklist

### Pre-Deployment

- [ ] All tests passing (`pytest`)
- [ ] Environment variables configured
- [ ] Database migrated to PostgreSQL
- [ ] Seed data loaded
- [ ] SSL certificate obtained
- [ ] Nginx configured
- [ ] Systemd services created
- [ ] Firewall rules set
- [ ] Backup script tested
- [ ] Health check endpoint verified

### Deployment

- [ ] Pull latest code on server
- [ ] Activate virtual environment
- [ ] Install/update dependencies (`pip install -r requirements.txt`)
- [ ] Run database migrations (if any)
- [ ] Restart services (`sudo systemctl restart medibridge-*`)
- [ ] Verify services running (`systemctl status`)
- [ ] Test Telegram bot (`/start` command)
- [ ] Test dashboard (open in browser)
- [ ] Check logs for errors (`journalctl -u medibridge-bot -f`)

### Post-Deployment

- [ ] Monitor logs for 1 hour
- [ ] Verify proposal workflow (send test message)
- [ ] Verify confirmation flow
- [ ] Verify undo flow
- [ ] Check database for errors
- [ ] Test search functionality
- [ ] Document any issues

---

## 16. Troubleshooting

### Issue: Bot Not Responding

**Check service status:**
```bash
sudo systemctl status medibridge-bot
```

**Check logs:**
```bash
sudo journalctl -u medibridge-bot -f
```

**Common causes:**
- Invalid `TELEGRAM_BOT_TOKEN`
- Webhook URL not accessible (firewall/SSL issue)
- Database connection failed

**Fix:**
```bash
# Restart service
sudo systemctl restart medibridge-bot

# Test webhook manually
curl -X POST https://yourdomain.com/telegram/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 1, "message": {"text": "/start"}}'
```

---

### Issue: Dashboard Not Loading

**Check service:**
```bash
sudo systemctl status medibridge-dashboard
```

**Check Streamlit logs:**
```bash
sudo journalctl -u medibridge-dashboard -f
```

**Test direct access:**
```bash
curl http://localhost:8501
```

**Common causes:**
- Port 8501 not open
- Nginx misconfigured
- Database connection failed

---

### Issue: Proposals Not Being Created

**Check database connection:**
```python
from medibridge_db.database import get_session

with get_session() as session:
    result = session.execute("SELECT COUNT(*) FROM pharmacies")
    print(result.scalar())
```

**Check user authorization:**
```python
from medibridge_db.operations import is_user_linked_to_pharmacy

with get_session() as session:
    linked = is_user_linked_to_pharmacy(session, user_id=1, pharmacy_id=1)
    print(f"User linked: {linked}")
```

**Check parser:**
```python
from medibridge.services.ai_parser import parse_stock_message

parsed = parse_stock_message("Dolo 650 khatam")
print(parsed)
```

---

## 17. Rollback Plan

If deployment fails:

**Step 1: Stop new services**
```bash
sudo systemctl stop medibridge-bot
sudo systemctl stop medibridge-dashboard
```

**Step 2: Restore database backup**
```bash
gunzip < /var/backups/medibridge/medibridge_YYYYMMDD_HHMMSS.sql.gz | \
  psql -U medibridge_user medibridge
```

**Step 3: Revert code**
```bash
cd /opt/medibridge
git checkout <previous-commit-hash>
```

**Step 4: Restart old services**
```bash
sudo systemctl start medibridge-bot
sudo systemctl start medibridge-dashboard
```

---

## 18. Future Enhancements

### 18.1 Containerization (Docker)

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "bot_webhook:app"]
```

**Docker Compose:**
```yaml
version: '3.8'

services:
  postgres:
    image: postgres:14
    environment:
      POSTGRES_DB: medibridge
      POSTGRES_USER: medibridge_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine

  bot:
    build: .
    depends_on:
      - postgres
      - redis
    environment:
      DATABASE_URL: postgresql://medibridge_user:${DB_PASSWORD}@postgres:5432/medibridge
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}

  dashboard:
    build: .
    command: streamlit run medibridge_dashboard.py
    ports:
      - "8501:8501"
    depends_on:
      - postgres

volumes:
  postgres_data:
```

### 18.2 Cloud Deployment (AWS)

**Architecture:**
- **EC2** for application servers
- **RDS PostgreSQL** for database
- **ElastiCache Redis** for caching
- **Application Load Balancer** for HTTPS termination
- **S3** for backups
- **CloudWatch** for monitoring

### 18.3 Kubernetes (Future)

**For high availability and auto-scaling**

---

## Summary

**Current Status:** Local development ready  
**Production Readiness:** Database migration and webhook configuration required  
**Recommended First Deployment:** Single VPS (DigitalOcean/Linode) with PostgreSQL, Nginx, and systemd services

**Next Steps:**
1. Set up staging environment with PostgreSQL
2. Test webhook integration with Telegram
3. Configure Nginx reverse proxy
4. Set up automated backups
5. Deploy to production with monitoring

For questions or deployment assistance, refer to `docs/architecture.md` (system design) and `docs/integration.md` (API reference).
