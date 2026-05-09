# CodeMaster Rollback Guide

Rollback planning assumes the old Tencent Cloud server remains available while Alibaba Cloud is introduced as production.

## 1. Preconditions

- Keep Tencent Cloud running until Alibaba Cloud has been stable for at least 7 to 14 days.
- Treat Tencent Cloud PostgreSQL 15.16 as the migration baseline and keep Alibaba Cloud Docker on `postgres:15` during cutover.
- Keep a recent `pg_dump -Fc --no-owner --no-acl` backup before every production deployment.
- Keep a complete copy of `media/`.
- Freeze writes before the final migration window.
- Avoid any period where both Tencent Cloud and Alibaba Cloud accept writes.
- Do not combine rollback planning with a PostgreSQL major-version upgrade; upgrade PostgreSQL only after the server migration is stable and separately backed up.

## 2. Roll Back Application Code on Alibaba Cloud

If a deployment fails before DNS cutover:

```bash
cd /opt/codemaster_system
git log --oneline -5
git checkout <previous-good-commit>
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" ./deploy/scripts/deploy.sh
```

If a migration has already run, confirm whether the old code is compatible with the new schema before restarting old code. If not, restore the latest backup.

## 3. Restore Database Backup

Use the restore script only after confirming the target dump:

```bash
cd /opt/codemaster_system
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" ./deploy/scripts/restore_db.sh /opt/codemaster/backups/codemaster_YYYYmmdd_HHMMSS.dump
```

The script requires typing `RESTORE`. After restore, run:

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" ./deploy/scripts/check_prod.sh
```

## 4. Roll Back DNS to Tencent Cloud

If Alibaba Cloud fails after DNS cutover:

1. Freeze writes on Alibaba Cloud.
2. Stop or firewall Alibaba Cloud web traffic to avoid split writes.
3. Change DNS A record back to the Tencent Cloud public IP.
4. Lower TTL before migration windows when possible.
5. Confirm `/`, login, teacher pages, homework pages, and miniapp APIs on Tencent Cloud.

## 5. Avoid Double Writes

Do not allow teachers, students, parents, or miniapp clients to submit data to both servers. During rollback, pick exactly one writable server:

- Tencent Cloud for rollback.
- Alibaba Cloud for forward migration.

The other side should be stopped, firewalled, or clearly placed in read-only maintenance mode.

## 6. Final Migration Freeze

Before final cutover:

1. Announce a maintenance window.
2. Stop writes on Tencent Cloud.
3. Take a fresh database backup.
4. Sync `media/`.
5. Restore onto Alibaba Cloud.
6. Run `collectstatic` on Alibaba Cloud.
7. Run production checks.
8. Switch DNS.

Keep Tencent Cloud unchanged after the freeze so it remains a clean rollback target.

## 7. Tencent Cloud Codex Support Role

Tencent Cloud can remain as the Codex and AI-assisted troubleshooting host. Recommended SSH workflow:

```bash
ssh aliyun-codemaster
cd /opt/codemaster_system
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=100 web
docker compose -f docker-compose.prod.yml logs --tail=100 nginx
```

Use Tencent Cloud Codex for inspection, log review, config comparison, and drafting commands. Do not directly edit production `.env`, run destructive Docker commands, or alter production data without an explicit operator decision.

## 8. Commands to Avoid

Never run this on production:

```bash
docker compose down -v
```

It can remove volumes. Prefer `docker compose restart`, `docker compose up -d`, or a controlled restore from backup.
