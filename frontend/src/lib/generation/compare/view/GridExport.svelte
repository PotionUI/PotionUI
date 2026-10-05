<script lang="ts">
	import { onMount } from 'svelte';
	import { toasts } from '$lib/stores/toast';
	import HistoryStitchModal from '../../../../routes/history/components/HistoryStitchModal.svelte';
	import type { ActiveGrid } from '../compareStore.svelte';
	import { resolveCellFiles } from './cellFiles';
	import { buildCompareStitch, type CompareStitchRequest } from './stitchCompareSource';

	let {
		grid,
		seconds = new Map<string, number>(),
		onClose
	}: {
		grid: ActiveGrid;
		seconds?: Map<string, number>;
		onClose: () => void;
	} = $props();

	let request = $state<CompareStitchRequest | null>(null);

	onMount(() => {
		let stale = false;
		resolveCellFiles(grid).then((files) => {
			if (stale) return;
			const built = buildCompareStitch(grid, files, seconds);
			if (built.context.items.length === 0) {
				toasts.error('No finished images to export yet');
				onClose();
				return;
			}
			request = built;
		});
		return () => {
			stale = true;
		};
	});
</script>

{#if request}
	<HistoryStitchModal context={request.context} compare={request.compare} {onClose} onDone={onClose} />
{/if}
