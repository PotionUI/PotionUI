<script lang="ts">
	// Console-bar readout-cell idiom, reused for the detail page's stat row -
	// same label/value shape as the generation panel's session cluster, just
	// laid out in a strip instead of a bar.
	import type { AdminGenerationListItem } from '$lib/services/admin-api';
	import { formatDurationMs } from '$lib/components/generation-panel/barState';
	import ReadoutCell from '$lib/components/generation-panel/ReadoutCell.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let { generation, pipeCount }: { generation: AdminGenerationListItem; pipeCount: number } = $props();

	let durationLabel = $derived.by(() => {
		if (!generation.completed_at) return generation.status === 'running' ? 'running' : 'none';
		const ms = new Date(generation.completed_at).getTime() - new Date(generation.created_at).getTime();
		return Number.isFinite(ms) && ms >= 0 ? formatDurationMs(ms) : 'none';
	});

	let fileCount = $derived(generation.files?.length ?? 0);
	let seedLabel = $derived(generation.seed != null && generation.seed !== -1 ? String(generation.seed) : 'random');
</script>

<div class="flex items-stretch divide-x divide-line bg-surface-1 border border-line rounded-lg overflow-x-auto">
	<ReadoutCell label="duration">{durationLabel}</ReadoutCell>
	<ReadoutCell label="pipes">{pipeCount || 'none'}</ReadoutCell>
	<ReadoutCell label="files">{fileCount}</ReadoutCell>
	<ReadoutCell label="seed">{seedLabel}</ReadoutCell>
	<ReadoutCell label="rating" mono={false}>
		<span class="flex items-center gap-1">
			{#if generation.is_favorite}
				<Icon name="star" className="w-3.5 h-3.5 text-warning" strokeWidth={2.5} />
			{/if}
			<span class="font-mono tabular-nums">{generation.rating > 0 ? generation.rating : '-'}</span>
		</span>
	</ReadoutCell>
</div>
