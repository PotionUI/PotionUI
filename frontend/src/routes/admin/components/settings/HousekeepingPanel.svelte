<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import type { HousekeepingOverview, HousekeepingRun } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Button, Alert, Spinner, Input } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import { formatBytes, formatCount } from '$lib/utils/format';

	let {
		settings,
		onSettingChange,
		savedSnapshot
	}: {
		settings: Record<string, any>;
		onSettingChange: (key: string, value: unknown) => void;
		/** The tab's saved-baseline snapshot (`snapshot`) - bumping it means
		 * the shared footer just persisted a batch that may include our three
		 * retention keys, so the preview counts are stale. */
		savedSnapshot: string;
	} = $props();

	let loading = $state(true);
	let error = $state<string | null>(null);
	let overview = $state<HousekeepingOverview | null>(null);
	let runInFlight = $state(false);

	function toNumber(value: unknown, fallback: number): number {
		const n = typeof value === 'string' ? Number(value) : value;
		return typeof n === 'number' && Number.isFinite(n) ? n : fallback;
	}

	let tmpDays = $derived(toNumber(settings.tmp_retention_days, 7));
	let runReportDays = $derived(toNumber(settings.run_report_retention_days, 30));
	let llmTraceDays = $derived(toNumber(settings.llm_trace_retention_days, 7));

	onMount(load);

	// The shared save bar persists our keys on its own schedule - once it
	// does, `savedSnapshot` (the tab's `snapshot`) moves and the preview
	// counts need a refresh. The initial run is a no-op: `loading` is still
	// true at that point, before onMount's own load() has resolved.
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
			const response = await adminApi.getHousekeeping();
			if (!response.success || !response.data) {
				error = response.message ?? 'Failed to load housekeeping settings.';
				return;
			}
			overview = response.data;
		} catch (e: any) {
			logger.error('Failed to load housekeeping settings:', e);
			error = e.response?.data?.message || e.message || 'Failed to load housekeeping settings.';
		} finally {
			loading = false;
		}
	}

	function updateField(key: string, raw: string) {
		const parsed = Number(raw);
		onSettingChange(key, Number.isFinite(parsed) ? parsed : 0);
	}

	function summarize(run: HousekeepingRun): string {
		const rows = run.tasks.run_reports.rows_removed + run.tasks.llm_traces.rows_removed;
		return `Freed ${formatBytes(run.tasks.tmp.bytes_freed)} · ${formatCount(rows)} rows removed`;
	}

	function lastRunLabel(run: HousekeepingRun | null): string {
		if (!run) return 'Never run';
		const started = new Date(run.started_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
		const rows = run.tasks.run_reports.rows_removed + run.tasks.llm_traces.rows_removed;
		return `Last run: ${started} · freed ${formatBytes(run.tasks.tmp.bytes_freed)} · ${formatCount(rows)} rows`;
	}

	async function runNow() {
		runInFlight = true;
		try {
			const response = await adminApi.runHousekeeping();
			if (response.success && response.data) {
				toasts.success(summarize(response.data));
				await load();
			} else {
				toasts.error(response.message ?? 'Failed to run housekeeping.');
			}
		} catch (e: any) {
			const status = e?.response?.status;
			const message = e.response?.data?.message || e.message || 'Failed to run housekeeping.';
			toasts.error(message);
			if (status === 409) await load();
		} finally {
			runInFlight = false;
		}
	}
</script>

<DetailSection label="Housekeeping" padded={false}>
	<div class="px-4 sm:px-5 py-4">
		<p class="text-sm text-fg-muted">Scheduled cleanup of scratch files and old run history.</p>
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
				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="housekeeping-tmp-retention" class="block text-sm font-medium text-fg mb-1">Scratch files</label>
						<p class="text-sm text-fg-muted">
							Files in the scratch folder older than this are deleted. 0 keeps everything.
						</p>
						{#if overview.preview.tmp.files > 0}
							<p class="font-mono text-2xs tabular-nums text-fg-subtle mt-1">
								would remove {formatBytes(overview.preview.tmp.bytes)} · {formatCount(overview.preview.tmp.files)} files
							</p>
						{:else}
							<p class="font-mono text-2xs tabular-nums text-fg-subtle mt-1">nothing to remove</p>
						{/if}
					</div>
					<Input
						id="housekeeping-tmp-retention"
						type="number"
						min="0"
						max="3650"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(tmpDays)}
						oninput={(e: Event) => updateField('tmp_retention_days', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="housekeeping-run-report-retention" class="block text-sm font-medium text-fg mb-1">Run reports</label>
						<p class="text-sm text-fg-muted">
							Generation run reports older than this are deleted. 0 keeps everything.
						</p>
						{#if overview.preview.run_reports > 0}
							<p class="font-mono text-2xs tabular-nums text-fg-subtle mt-1">
								would remove {formatCount(overview.preview.run_reports)} rows
							</p>
						{:else}
							<p class="font-mono text-2xs tabular-nums text-fg-subtle mt-1">nothing to remove</p>
						{/if}
					</div>
					<Input
						id="housekeeping-run-report-retention"
						type="number"
						min="0"
						max="3650"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(runReportDays)}
						oninput={(e: Event) => updateField('run_report_retention_days', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="housekeeping-llm-trace-retention" class="block text-sm font-medium text-fg mb-1">Chat traces</label>
						<p class="text-sm text-fg-muted">
							Chat LLM call traces older than this are deleted. 0 keeps everything.
						</p>
						{#if overview.preview.llm_traces > 0}
							<p class="font-mono text-2xs tabular-nums text-fg-subtle mt-1">
								would remove {formatCount(overview.preview.llm_traces)} rows
							</p>
						{:else}
							<p class="font-mono text-2xs tabular-nums text-fg-subtle mt-1">nothing to remove</p>
						{/if}
					</div>
					<Input
						id="housekeeping-llm-trace-retention"
						type="number"
						min="0"
						max="3650"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(llmTraceDays)}
						oninput={(e: Event) => updateField('llm_trace_retention_days', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>
			</div>

			<div class="px-4 sm:px-5 py-4 border-t border-line flex items-center justify-between gap-3">
				<p class="font-mono text-xs tabular-nums text-fg-muted">{lastRunLabel(overview.last_run)}</p>
				<Button variant="secondary" size="sm" loading={runInFlight} disabled={runInFlight || overview.running} onclick={runNow}>
					Run now
				</Button>
			</div>
		{/if}
	{/if}
</DetailSection>
