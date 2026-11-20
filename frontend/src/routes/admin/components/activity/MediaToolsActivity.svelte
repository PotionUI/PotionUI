<script lang="ts">
	import { onMount } from 'svelte';
	import type { MediaToolPool } from '$lib/services/api/mediaToolsPool';
	import { Alert, Button, LoadErrorState, Spinner } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import MediaToolsJobs from './MediaToolsJobs.svelte';
	import {
		assessPool,
		formatPercent,
		formatWait,
		mapPoolRows
	} from './mediaToolsPool';

	let {
		pool,
		loaded,
		loadError,
		onReload
	}: {
		pool: MediaToolPool | null;
		loaded: boolean;
		loadError: string | null;
		onReload: () => void | Promise<void>;
	} = $props();

	let now = $state(Date.now());
	let fetchedAt = $state(Date.now());
	let seenPool: MediaToolPool | null = null;

	$effect(() => {
		if (pool !== seenPool) {
			seenPool = pool;
			fetchedAt = Date.now();
			now = fetchedAt;
		}
	});

	const assessment = $derived(pool ? assessPool(pool) : null);
	const rows = $derived(pool ? mapPoolRows(pool.jobs, fetchedAt, now) : []);
	const rejected = $derived(pool ? pool.metrics.rejected_queue_full + pool.metrics.rejected_preview_busy : 0);

	onMount(() => {
		const clock = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(clock);
	});

	function fill(used: number, total: number): number {
		return total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
	}
</script>

{#snippet meter(used: number, total: number)}
	<div
		class="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-3"
		role="progressbar"
		aria-valuemin="0"
		aria-valuemax={total}
		aria-valuenow={used}
		aria-label="Slots in use"
	>
		<div
			class="h-full transition-[width] {used >= total && total > 0 ? 'bg-warning-solid' : 'bg-signal'}"
			style="width: {fill(used, total)}%"
		></div>
	</div>
{/snippet}

{#snippet figure(label: string, value: string)}
	<div class="min-w-0">
		<span class="block text-xs text-fg-subtle mb-1">{label}</span>
		<span class="block font-mono text-lg font-semibold tabular-nums text-fg">{value}</span>
	</div>
{/snippet}

{#if !loaded}
	<div class="flex items-center justify-center py-16">
		<Spinner size="lg" />
	</div>
{:else if !pool}
	<LoadErrorState
		title="Could not load the media tools pool"
		message={loadError ?? 'Something went wrong.'}
		onRetry={onReload}
	/>
{:else}
	{#if loadError}
		<Alert variant="warning" density="compact" icon>
			Live updates paused: {loadError}
		</Alert>
	{/if}

	<DetailSection label="Usage">
		<div class="space-y-4" data-pool-usage>
			{#if assessment}
				{#if assessment.tone === 'ok'}
					<p class="flex items-center gap-2 text-sm text-fg-muted" data-pool-status="ok">
						<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-success" aria-hidden="true"></span>
						{assessment.message}
					</p>
				{:else}
					<Alert variant={assessment.tone === 'danger' ? 'danger' : 'warning'} icon live="polite">
						<span data-pool-status={assessment.tone}>{assessment.message}</span>
						{#snippet actions()}
							<Button size="sm" variant="secondary" href="/admin?tab=settings&view=media_tools">Change limits</Button>
						{/snippet}
					</Alert>
				{/if}
			{/if}

			<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
				<div class="rounded-lg border border-line bg-surface-1 p-4" data-pool-tile="runs">
					<span class="block text-xs text-fg-subtle mb-1">Running</span>
					<span class="font-mono text-2xl font-semibold tabular-nums text-fg">
						{pool.running} <span class="text-fg-subtle">/ {pool.limits.max_concurrent}</span>
					</span>
					{@render meter(pool.running, pool.limits.max_concurrent)}
				</div>
				<div class="rounded-lg border border-line bg-surface-1 p-4" data-pool-tile="previews">
					<span class="block text-xs text-fg-subtle mb-1">Previews</span>
					<span class="font-mono text-2xl font-semibold tabular-nums text-fg">
						{pool.previews.in_use} <span class="text-fg-subtle">/ {pool.previews.limit}</span>
					</span>
					{@render meter(pool.previews.in_use, pool.previews.limit)}
				</div>
				<div class="rounded-lg border border-line bg-surface-1 p-4" data-pool-tile="queue">
					<span class="block text-xs text-fg-subtle mb-1">Waiting</span>
					<span class="font-mono text-2xl font-semibold tabular-nums {pool.queued > 0 ? 'text-warning' : 'text-fg'}">
						{pool.queued}
					</span>
					<p class="mt-3 font-mono text-xs tabular-nums text-fg-subtle">{pool.cpu_cores} CPU cores</p>
				</div>
			</div>
		</div>
	</DetailSection>

	<DetailSection label="Last hour">
		<div class="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6" data-pool-metrics>
			{@render figure('Peak running', String(pool.metrics.peak_concurrent))}
			{@render figure('Peak waiting', String(pool.metrics.peak_queued))}
			{@render figure('Average wait', formatWait(pool.metrics.wait_avg_seconds))}
			{@render figure('Longest wait', formatWait(pool.metrics.wait_max_seconds))}
			{@render figure('Refused', String(rejected))}
			{@render figure(
				'Busy 5 min / 60 min',
				`${formatPercent(pool.metrics.utilisation_5m_percent)} / ${formatPercent(pool.metrics.utilisation_60m_percent)}`
			)}
		</div>
	</DetailSection>

	<MediaToolsJobs {rows} onChanged={onReload} />
{/if}
