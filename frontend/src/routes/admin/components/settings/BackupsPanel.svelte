<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import type { BackupArchive, BackupJob, BackupOverview, BackupTier } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Button, IconButton, Alert, Spinner, Input, Badge, SegmentedControl, CopyButton } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { formatBytes } from '$lib/utils/format';

	let {
		settings,
		onSettingChange,
		savedSnapshot
	}: {
		settings: Record<string, any>;
		onSettingChange: (key: string, value: unknown) => void;
		/** The tab's saved-baseline snapshot (`snapshot`) - bumping it means
		 * the shared footer just persisted a batch that may include our three
		 * keys, so the destination probe and archive list are stale. It is also
		 * where "Backup now" reads the tier from: a run uses what the server
		 * has, not the unsaved edit sitting in the form. */
		savedSnapshot: string;
	} = $props();

	const TIER_ITEMS = [
		{ id: 'config', label: 'Config' },
		{ id: 'media', label: 'Media' },
		{ id: 'all', label: 'All' }
	];

	const TIER_HINTS: Record<BackupTier, string> = {
		config: 'Database, secret key, .env, local presets, plugins and automation, and the small storage trees.',
		media: 'Everything in config plus a mirror of uploads and generations.',
		all: 'Everything in media plus the models directory.'
	};

	let loading = $state(true);
	let error = $state<string | null>(null);
	let overview = $state<BackupOverview | null>(null);
	let job = $state<BackupJob | null>(null);
	let runInFlight = $state(false);
	let now = $state(Date.now());
	let deleteTarget = $state<BackupArchive | null>(null);
	let deleting = $state(false);

	let pollHandle: ReturnType<typeof setInterval> | null = null;

	function isTier(value: unknown): value is BackupTier {
		return value === 'config' || value === 'media' || value === 'all';
	}

	let destination = $derived(
		typeof settings.backup_destination === 'string'
			? settings.backup_destination
			: (overview?.settings.destination ?? '')
	);
	let retention = $derived(
		Number.isFinite(Number(settings.backup_retention))
			? Number(settings.backup_retention)
			: (overview?.settings.retention ?? 0)
	);
	let tier = $derived(
		isTier(settings.backup_default_tier) ? settings.backup_default_tier : (overview?.settings.default_tier ?? 'config')
	);

	/** The tier a run uses: the persisted value, never the dirty form edit. */
	let savedTier = $derived.by(() => {
		try {
			const saved = JSON.parse(savedSnapshot) as Record<string, unknown>;
			if (isTier(saved.backup_default_tier)) return saved.backup_default_tier;
		} catch {
			/* an unparseable snapshot falls back to the server's value */
		}
		return overview?.settings.default_tier ?? 'config';
	});

	let jobRunning = $derived(job?.status === 'running');
	let archives = $derived(overview?.archives ?? []);

	let elapsed = $derived.by(() => {
		if (!job?.started_at) return '';
		const started = Date.parse(job.started_at);
		if (!Number.isFinite(started)) return '';
		const seconds = Math.max(0, Math.round((now - started) / 1000));
		if (seconds < 60) return `${seconds}s`;
		return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
	});

	onMount(load);
	onDestroy(stopPolling);

	// The shared save bar persists our keys on its own schedule - once it
	// does, `savedSnapshot` (the tab's `snapshot`) moves and the destination
	// probe, retention pruning and archive list need a refresh. The initial
	// run is a no-op: `loading` is still true at that point, before onMount's
	// own load() has resolved.
	$effect(() => {
		savedSnapshot;
		untrack(() => {
			if (!loading) load();
		});
	});

	async function load() {
		loading = true;
		error = null;
		try {
			const response = await adminApi.getBackups();
			if (!response.success || !response.data) {
				error = response.message ?? 'Failed to load backup settings.';
				return;
			}
			overview = response.data;
			job = response.data.job;
			syncPolling();
		} catch (e: any) {
			logger.error('Failed to load backup settings:', e);
			error = e.response?.data?.message || e.message || 'Failed to load backup settings.';
		} finally {
			loading = false;
		}
	}

	function updateRetention(raw: string) {
		const parsed = Number(raw);
		onSettingChange('backup_retention', Number.isFinite(parsed) ? parsed : 0);
	}

	function startPolling() {
		stopPolling();
		now = Date.now();
		pollHandle = setInterval(pollJob, 2000);
	}

	function stopPolling() {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	}

	function syncPolling() {
		if (job?.status === 'running') startPolling();
		else stopPolling();
	}

	async function pollJob() {
		now = Date.now();
		try {
			const response = await adminApi.getBackupJob();
			if (!response.success) return;
			const next = response.data ?? null;
			job = next;
			if (!next || next.status !== 'running') {
				stopPolling();
				if (next?.status === 'failed') toasts.error(next.last_error ?? 'Backup failed.');
				else if (next?.status === 'done') toasts.success(`Backup finished · ${formatBytes(next.bytes)}`);
				await load();
			}
		} catch (e) {
			logger.error('Failed to poll backup job:', e);
		}
	}

	async function runNow() {
		runInFlight = true;
		try {
			const response = await adminApi.runBackup(savedTier);
			if (response.success && response.data) {
				job = response.data;
				syncPolling();
			} else {
				toasts.error(response.message ?? 'Failed to start backup.');
			}
		} catch (e: any) {
			const status = e?.response?.status;
			toasts.error(e.response?.data?.message || e.message || 'Failed to start backup.');
			if (status === 409) await load();
		} finally {
			runInFlight = false;
		}
	}

	function askDelete(archive: BackupArchive) {
		deleteTarget = archive;
	}

	function cancelDelete() {
		deleteTarget = null;
	}

	async function confirmDelete() {
		const target = deleteTarget;
		if (!target) return;
		deleting = true;
		try {
			const response = await adminApi.deleteBackup(target.name);
			deleteTarget = null;
			if (response.success) await load();
			else toasts.error(response.message ?? 'Failed to delete the archive.');
		} catch (e: any) {
			deleteTarget = null;
			toasts.error(e.response?.data?.message || e.message || 'Failed to delete the archive.');
		} finally {
			deleting = false;
		}
	}

	function formatTime(value: string | null): string {
		if (!value) return 'unknown';
		const parsed = Date.parse(value);
		if (!Number.isFinite(parsed)) return value;
		return new Date(parsed).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
	}

	function lastBackupLabel(o: BackupOverview): string {
		const last = o.last_backup;
		if (!last || !last.time) return 'Last backup: never';
		return `Last backup: ${formatTime(last.time)} · ${formatBytes(last.bytes)} · ${last.tier ?? 'unknown'}`;
	}

	function archiveTiers(archive: BackupArchive): string[] {
		if (archive.tiers.length > 0) return archive.tiers;
		return archive.tier ? [archive.tier] : [];
	}
</script>

<DetailSection label="Backups" padded={false}>
	<div class="px-4 sm:px-5 py-4">
		<p class="text-sm text-fg-muted">Archives of the database and the files it points at.</p>
	</div>

	{#if loading}
		<div class="px-4 sm:px-5 pb-4">
			<Spinner size="sm" />
		</div>
	{:else}
		{#if error}
			<div class="px-4 sm:px-5 pb-4">
				<Alert variant="danger" icon>{error}</Alert>
			</div>
		{/if}

		{#if overview}
			<div class="px-4 sm:px-5 divide-y divide-line">
				<div class="py-4 space-y-2">
					<label for="backup-destination" class="block text-sm font-medium text-fg">Destination</label>
					<p class="text-sm text-fg-muted">Where archives are written. Relative paths resolve against the app directory.</p>
					<Input
						id="backup-destination"
						class="font-mono"
						value={destination}
						oninput={(e: Event) => onSettingChange('backup_destination', (e.currentTarget as HTMLInputElement).value)}
					/>
					<p class="font-mono text-2xs tabular-nums text-fg-subtle">{overview.destination_abs}</p>
					{#if !overview.writable}
						<Alert variant="danger" icon density="compact">
							Not writable: {overview.destination_abs}
						</Alert>
					{:else if !overview.exists}
						<p class="text-2xs text-fg-subtle">
							Will be created on the first backup: <span class="font-mono tabular-nums">{overview.destination_abs}</span>
						</p>
					{/if}
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="backup-retention" class="block text-sm font-medium text-fg mb-1">Retention</label>
						<p class="text-sm text-fg-muted">Archives to keep; 0 keeps all.</p>
					</div>
					<Input
						id="backup-retention"
						type="number"
						min="0"
						max="365"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(retention)}
						oninput={(e: Event) => updateRetention((e.currentTarget as HTMLInputElement).value)}
					/>
				</div>

				<div class="py-4 space-y-2">
					<p class="text-sm font-medium text-fg">Default tier</p>
					<SegmentedControl items={TIER_ITEMS} selected={tier} onSelect={(id) => onSettingChange('backup_default_tier', id)} ariaLabel="Default backup tier" />
					<p class="text-sm text-fg-muted">{TIER_HINTS[tier]}</p>
				</div>
			</div>

			<div class="px-4 sm:px-5 py-4 border-t border-line space-y-2">
				<div class="flex items-center justify-between gap-3">
					<p class="font-mono text-xs tabular-nums text-fg-muted">{lastBackupLabel(overview)}</p>
					<Button variant="secondary" size="sm" loading={runInFlight} disabled={runInFlight || jobRunning} onclick={runNow}>
						Backup now
					</Button>
				</div>
				{#if job && jobRunning}
					<p class="font-mono text-2xs tabular-nums text-fg-subtle truncate">
						{job.tier} · running · {elapsed}{job.bytes > 0 ? ` · ${formatBytes(job.bytes)}` : ''}
					</p>
				{:else if job?.status === 'failed' && job.last_error}
					<Alert variant="danger" icon density="compact">{job.last_error}</Alert>
				{/if}
			</div>

			<div class="px-4 sm:px-5 py-4 border-t border-line space-y-2">
				<p class="text-sm font-medium text-fg">Archives</p>
				{#if archives.length === 0}
					<p class="text-sm text-fg-muted">No archives yet.</p>
				{:else}
					<ul class="max-h-64 overflow-y-auto divide-y divide-line rounded border border-line-strong">
						{#each archives as archive (archive.name)}
							<li class="flex items-center gap-3 px-3 py-2">
								<div class="min-w-0 flex-1">
									<p class="font-mono text-xs tabular-nums text-fg truncate" title={archive.name}>{archive.name}</p>
									<p class="font-mono text-2xs tabular-nums text-fg-subtle">
										{formatTime(archive.created_at)} · {formatBytes(archive.bytes)}{archive.app_version
											? ` · ${archive.app_version}`
											: ''}
									</p>
								</div>
								<div class="flex items-center gap-1 flex-shrink-0">
									{#each archiveTiers(archive) as t (t)}
										<Badge variant="neutral" size="sm">{t}</Badge>
									{/each}
								</div>
								<IconButton
									icon="trash"
									size="sm"
									label={`Delete ${archive.name}`}
									onclick={() => askDelete(archive)}
								/>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			{#if overview.mirror}
				<div class="px-4 sm:px-5 py-4 border-t border-line space-y-1">
					<p class="text-sm font-medium text-fg">Media mirror</p>
					<p class="font-mono text-xs tabular-nums text-fg-muted">
						{overview.mirror.last_synced ? formatTime(overview.mirror.last_synced) : 'never synced'} · {overview.mirror
							.day_count} days · {formatBytes(overview.mirror.bytes)}
					</p>
				</div>
			{/if}

			<div class="px-4 sm:px-5 py-4 border-t border-line space-y-2">
				<p class="text-sm font-medium text-fg">Scheduled backups</p>
				<div class="flex items-center gap-2">
					<code class="flex-1 min-w-0 rounded border border-line-strong bg-surface-2 px-2.5 py-1.5 font-mono text-2xs text-fg-muted overflow-x-auto whitespace-pre">{overview.cron_line}</code>
					<CopyButton text={overview.cron_line} title="Copy cron line" size="sm" />
				</div>
				<p class="text-sm text-fg-muted">
					Restore runs from the command line with the app stopped.
					<a href="/admin?tab=docs&doc=user/backup-and-restore" class="text-signal hover:underline">Backup and restore</a>
				</p>
			</div>
		{/if}
	{/if}
</DetailSection>

<ConfirmModal
	isOpen={deleteTarget !== null}
	title="Delete archive"
	message={deleteTarget ? `${deleteTarget.name} (${formatBytes(deleteTarget.bytes)}) is removed from disk. This cannot be undone.` : ''}
	variant="danger"
	busy={deleting}
	on:confirm={confirmDelete}
	on:cancel={cancelDelete}
/>
