<script lang="ts">
	import { onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { getMediaToolPool, type MediaToolPool } from '$lib/services/api/mediaToolsPool';
	import { Button, LoadErrorState, Spinner } from '$lib/components/ui';
	import MediaToolsLimits from './MediaToolsLimits.svelte';

	let { settings }: { settings: Record<string, unknown> } = $props();

	let pool = $state<MediaToolPool | null>(null);
	let loadError = $state<string | null>(null);
	let loaded = $state(false);

	async function load() {
		try {
			pool = await getMediaToolPool();
			loadError = null;
		} catch (error) {
			logger.error('Failed to load the media tools pool:', error);
			loadError = error instanceof Error ? error.message : 'Could not load the media tools pool.';
		} finally {
			loaded = true;
		}
	}

	onMount(() => void load());
</script>

{#if !loaded}
	<div class="flex items-center justify-center py-16">
		<Spinner size="lg" />
	</div>
{:else if !pool}
	<LoadErrorState
		title="Could not load the media tools pool"
		message={loadError ?? 'Something went wrong.'}
		onRetry={load}
	/>
{:else}
	<div class="flex flex-wrap items-center justify-between gap-3" data-pool-usage-line>
		<p class="font-mono text-sm tabular-nums text-fg-muted">
			{pool.running} / {pool.limits.max_concurrent} running, {pool.queued} waiting
		</p>
		<Button size="sm" variant="secondary" href="/admin?tab=generations&view=media_tools">See activity</Button>
	</div>
	<MediaToolsLimits {pool} {settings} onSaved={load} />
{/if}
