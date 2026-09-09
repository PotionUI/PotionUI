---
title: Backup & Restore
order: 85
---

# Backup & Restore

PotionUI's state isn't just the database. Your generation history, the media files it points to, your local presets, plugins and automation templates, your credentials, and your saved prompts, phrasebook entries and profile avatars are all spread across the database and several directories on disk. A useful backup has to gather all of that consistently, not just copy `db.sqlite`. The `./potionui backup` and `./potionui restore` commands do this for you.

## What is backed up

`./potionui backup [--out DIR] [--tier config|media|all]` writes into `--out` (default `./backups`). There are three tiers, each a superset of the one before it:

| Tier | What it includes |
| --- | --- |
| `config` (default) | A single zip: the database, `secret.key`, `.env` if present, your local presets/plugins/automation, small storage config files (`storage/llm.yml`, saved prompts, segment categories/templates), and small storage trees (avatars, profiles, preset media, inspirations, lora-dataset, spritesheet, video-editor, remote imports). |
| `media` | Everything in `config`, plus a directory mirror of your generated and uploaded media (`uploads/...` and `generations/<date>/<id>/...`) written to `<out>/media/`. |
| `all` | Everything in `media`, plus a mirror of your models directory. Same as passing `--include-models` with `--tier media`. |

The database is captured with SQLite's `VACUUM INTO`, which reads through the write-ahead log to produce a consistent snapshot. A plain file copy of `db.sqlite` would miss anything still sitting in the WAL, so the backup never does that.

The marketplace presets, plugins and automation templates aren't backed up — they're tracked in the repository itself, not local state. Only your `content/*/local` trees are included.

A few things are deliberately left out of every tier: `storage/tmp`, `storage/chromadb`, `storage/logs`, `storage/model_hash_cache.json`, `storage/settings.json` (the settings table in the database is the real source of truth, this file is not), any `*.bak-*` file, and the SQLite `-wal`/`-shm` sidecars.

The archive itself is a single zip named `potionui-backup-<YYYYMMDD-HHMMSS>.zip`, plus a `manifest.json` describing what it contains. The command exits `0` on success and `1` if it's refused or fails.

## Animated thumbnails

By default, `--tier media` and `--tier all` skip `*_animated.webp` files — the short looping preview generated for videos. These are rebuilt from the source video by the thumbnail job, so backing them up separately is redundant. Pass `--include-animated-thumbnails` if you want them mirrored anyway (for example, if you don't want to wait for them to regenerate after a restore).

## Mirror semantics

The media (and models) mirror under `<out>/media/` is a plain directory tree, not a zip, and it's incremental: a file whose size and modification time already match the destination is skipped, so a nightly backup only copies what changed since the last run.

The mirror never deletes anything. If you delete a generation inside PotionUI, its files stay in the mirror — nothing prunes it automatically. If you want the mirror to reflect deletions, you need to prune it yourself.

The database snapshot is always taken before the media copy runs. PotionUI writes an output file to disk before it writes the database row that points at it, so by the time the mirror walks the media directories, every row already in the snapshot has its file on disk.

## Restore onto a fresh install

Check the plan first with `--dry-run` — it prints what would happen and the verification result without writing anything.

1. Check out the same or a newer PotionUI version as the one the backup came from. Restoring a backup whose `migration_head` names a migration this checkout doesn't have is refused — you can restore old backups onto a newer install, but not the other way around.
2. Run `./potionui start` far enough that it creates the `storage/` directory, then stop it with `./potionui stop`. Restore also refuses to run while PotionUI is running.
3. Run `./potionui restore /path/to/potionui-backup-....zip`. By default it looks for a `media` directory sitting next to the archive; pass `--media DIR` to point at a different mirror location.
4. Start PotionUI again with `./potionui start`. Restore doesn't run database migrations itself — normal startup does, so restoring an older backup onto a newer checkout is fine as long as you let it boot afterward.

Restore unpacks the archive to a staging directory, validates the manifest, then moves each item into place one at a time. Anything it replaces is kept rather than deleted: the previous database is renamed to `db.sqlite.pre-restore-<timestamp>` (with its `-wal`/`-shm` sidecars renamed alongside it), and the previous encryption key and each replaced content or storage tree get a `.pre-restore-<timestamp>` name too. Delete those yourself once you've confirmed the restore is good.

After moving files into place, restore copies the media mirror back, skipping any file already present in the install, and then verifies the result: it opens the restored database and checks that every generation file, thumbnail and upload it references actually exists on disk. Missing animated thumbnails are summarized as a count, since they're expected to be absent and will be rebuilt; a missing original file is listed with its generation id so you can investigate.

Restore does not bring your models back — see below.

## Models

Model files are excluded from backups unless you ask for them (`--tier all` or `--tier media --include-models`). They're large and re-downloadable: the downloader plugin fetches them on demand, and the database's model records carry the hashes needed to find the right file again. If you do back up your models directory, restore doesn't put it back automatically — copy the mirror across by hand, or just let the models re-download.

## Backing up with S3 storage

If your storage backend is configured as `s3`, `--tier media` and `--tier all` are refused — your media already lives in the S3 bucket, not on the local disk, so use your bucket's own versioning for that. `--tier config` still works and is still worth running regularly, since it covers the database and your credentials.

## Scheduling

A nightly cron entry covering the database and media is enough for most installs:

```
0 3 * * * /path/to/potionui backup --out /mnt/backups --tier media
```

## The archive contains your encryption key

The backup archive bundles `secret.key`, the key PotionUI uses to encrypt every stored credential — provider API keys, backend configs, LLM keys. Anyone who has the archive can decrypt all of them. Treat a backup archive with exactly the same care as that key: store it somewhere access-controlled, and don't leave it in a shared or public location.
