# Odoo 19 Community Edition — Local Setup

> Gold Tier AI Employee | Accounting Integration

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- Ports 8069 and 5432 available (not used by other services)

## Quick Start

### 1. Start Odoo

From the `Gold-Tier/` directory:

```bash
docker-compose up -d
```

This pulls the images on first run (Odoo 19.0 + PostgreSQL 16) and starts both containers. The `-d` flag runs them in the background.

Wait for both containers to be healthy:

```bash
docker-compose ps
```

You should see both `odoo_gold_tier` and `odoo_gold_tier_db` with status `Up`.

### 2. Access Odoo

Open your browser and go to:

```
http://localhost:8069
```

### 3. First-Time Database Setup

On first launch, Odoo will initialize the `odoo_gold_tier` database automatically with the base module.

Once the page loads, you'll see the Odoo login screen. Log in with:

- **Email:** admin
- **Password:** admin

If you see the database manager instead, create the database with these settings:

| Field | Value |
|-------|-------|
| Master Password | admin |
| Database Name | odoo_gold_tier |
| Email | admin |
| Password | admin |
| Language | English (US) |
| Country | *(your country)* |
| Demo Data | **Unchecked** (leave empty for production) |

Click **Create Database** and wait for initialization.

### 4. Install Accounting Module

Once logged in:

1. Go to **Apps** in the top menu
2. Search for **Invoicing** (this is the free Community accounting module)
3. Click **Install**
4. Optionally also install **Expenses** and **Purchase**

These modules provide the accounting data our AI Employee will integrate with.

### 5. Configure for AI Integration

After installing accounting modules:

1. Go to **Settings** > **Technical** > **Database Structure** > **API Keys** (or enable Developer Mode first)
2. Create an API key or note the XML-RPC endpoint:
   - URL: `http://localhost:8069`
   - Database: `odoo_gold_tier`
   - Username: `admin`
   - Password: `admin`

Update the Gold-Tier `.env` file:

```
ODOO_URL=http://localhost:8069
ODOO_DB=odoo_gold_tier
ODOO_USERNAME=admin
ODOO_PASSWORD=admin
```

## Common Commands

### Stop Odoo (preserves data)

```bash
docker-compose down
```

### Stop and remove all data (fresh start)

```bash
docker-compose down -v
```

The `-v` flag removes the named volumes, erasing all Odoo and database data.

### View logs

```bash
# All services
docker-compose logs -f

# Odoo only
docker-compose logs -f odoo

# PostgreSQL only
docker-compose logs -f db
```

### Restart Odoo

```bash
docker-compose restart odoo
```

### Check container status

```bash
docker-compose ps
```

### Access PostgreSQL directly

```bash
docker exec -it odoo_gold_tier_db psql -U odoo -d odoo_gold_tier
```

### Access Odoo shell

```bash
docker exec -it odoo_gold_tier odoo shell -d odoo_gold_tier
```

## Architecture

```
docker-compose.yml
    |
    ├── odoo (odoo:19.0)
    │   ├── Port 8069 → localhost:8069
    │   ├── /var/lib/odoo      → odoo-data volume
    │   ├── /etc/odoo          → odoo-config volume
    │   └── /mnt/extra-addons  → odoo-addons volume
    │
    └── db (postgres:16)
        ├── Port 5432 → localhost:5432
        └── /var/lib/postgresql/data → odoo-db-data volume
```

## Volumes

| Volume | Purpose | Persists |
|--------|---------|----------|
| `odoo-db-data` | PostgreSQL database files | Yes |
| `odoo-data` | Odoo filestore (attachments, session data) | Yes |
| `odoo-config` | Odoo configuration files | Yes |
| `odoo-addons` | Custom/extra Odoo modules | Yes |

All data survives `docker-compose down`. Only `docker-compose down -v` removes it.

## Connecting from Python (XML-RPC)

The AI Employee will use `odoorpc` or `xmlrpc.client` to connect:

```python
import xmlrpc.client

url = "http://localhost:8069"
db = "odoo_gold_tier"
username = "admin"
password = "admin"

# Authenticate
common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, username, password, {})

# Call methods
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
partners = models.execute_kw(
    db, uid, password,
    'res.partner', 'search_read',
    [[['is_company', '=', True]]],
    {'fields': ['name', 'email'], 'limit': 5}
)
print(partners)
```

## Troubleshooting

### Port 5432 already in use
If you have a local PostgreSQL installation, either stop it or change the port mapping in `docker-compose.yml`:
```yaml
ports:
  - "5433:5432"  # Use 5433 on host instead
```

### Port 8069 already in use
Change the Odoo port mapping:
```yaml
ports:
  - "8070:8069"  # Use 8070 on host instead
```
Then access Odoo at `http://localhost:8070`.

### Odoo won't start (waiting for db)
Check that PostgreSQL is healthy:
```bash
docker-compose logs db
```
The Odoo container waits for the `db` healthcheck to pass before starting.

### Reset everything
```bash
docker-compose down -v
docker-compose up -d
```
This gives you a completely fresh Odoo instance.

## Security Notes

- Default credentials (`admin`/`admin`) are for local development only
- Change the master password and admin password before any production use
- PostgreSQL is exposed on port 5432 — restrict access if on a shared network
- The `docker-compose.yml` uses `restart: unless-stopped` so containers survive Docker Desktop restarts
