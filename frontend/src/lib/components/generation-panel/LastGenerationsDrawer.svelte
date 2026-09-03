<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner, Pagination } from '$lib/components/ui';
	import JustifiedGenerationGallery from '$lib/components/JustifiedGenerationGallery.svelte';
	import GenerationDetailsModal from '$lib/components/modals/GenerationDetailsModal.svelte';
	import { currentPageFromOffset, totalPagesFromCount, offsetForPage } from '$lib/utils/offsetPagination';

	// Recent-results drawer for the generation panel: the current user's last
	// generations for the preset selected in this tab. Mounted fresh each time
	// the drawer opens (GenerationPanel gates it with {#if}), so it always
	// fetches on open; `refreshSignal` lets the parent bump a counter to
	// refetch while it stays open (a generation completing on this tab).
	export let presetId: string | null = null;
	export let presetName: string | null | undefined = undefined;
	export let refreshSignal: number = 0;

	const LIMIT = 20;
	const dispatch = createEventDispatcher<{ reuse: GenerationHistoryItem }>();

	let generations: GenerationHistoryItem[] = [];
	let loading = true;
	let loadError = false;
	let loadedKey: string | null = null;
	let offset = 0;
	let total: number | null = null;

	let selectedGeneration: GenerationHistoryItem | null = null;
	let selectedFileIndex = 0;

	$: currentPage = currentPageFromOffset(offset, LIMIT);
	$: totalPages = totalPagesFromCount(total ?? 0, LIMIT);

	// A new preset or a generation completing on this tab always lands the
	// list back on page 1 — the fresh result belongs at the top of it.
	$: key = `${presetId ?? ''}:${refreshSignal}`;
	$: if (key !== loadedKey) {
		loadedKey = key;
		offset = 0;
		load();
	}

	async function load() {
		if (!presetId) {
			generations = [];
			loading = false;
			loadError = false;
			total = null;
			return;
		}
		loading = true;
		loadError = false;
		try {
			const response = await api.getGenerationHistory({
				presetId,
				limit: LIMIT,
				offset,
				sortBy: 'created_at',
				sortDir: 'desc'
			});
			if (response.success && response.data) {
				generations = response.data.generations;
				total = response.data.total;
			} else {
				generations = [];
				loadError = true;
			}
		} catch (err) {
			logger.error('Failed to load last generations:', err);
			generations = [];
			loadError = true;
		} finally {
			loading = false;
		}
	}

	function goToPage(page: number) {
		offset = offsetForPage(page, LIMIT);
		load();
	}

	function openGeneration(generation: GenerationHistoryItem, fileIndex: number) {
		selectedGeneration = generation;
		selectedFileIndex = fileIndex;
	}

	function handleModalClose() {
		selectedGeneration = null;
		selectedFileIndex = 0;
	}

	function handleReuse(generation: GenerationHistoryItem) {
		dispatch('reuse', generation);
	}
</script>

<div class="p-4">
	<div class="flex items-baseline gap-3 mb-3">
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted whitespace-nowrap">
			{presetName || 'This preset'}
		</span>
		{#if !loading && !loadError && total !== null && total > 0}
			<span class="font-mono tabular-nums text-2xs text-fg-subtle">{total}</span>
		{/if}
		<div class="flex-1 h-px bg-line self-center"></div>
	</div>

	{#if !presetId}
		<div class="dot-grid text-center py-10 rounded-lg">
			<p class="text-sm text-fg-subtle">Select a preset to see its recent generations.</p>
		</div>
	{:else if loading}
		<div class="flex items-center justify-center py-10">
			<Spinner size="md" />
		</div>
	{:else if loadError}
		<div class="dot-grid text-center py-10 rounded-lg">
			<div class="w-12 h-12 flex items-center justify-center mx-auto mb-3 rounded-lg bg-surface-2 border border-line">
				<Icon name="warning" className="h-6 w-6 text-fg-subtle" strokeWidth={1.5} />
			</div>
			<p class="text-sm text-fg-subtle">Couldn't load generations.</p>
		</div>
	{:else if generations.length > 0}
		<JustifiedGenerationGallery {generations} onOpen={openGeneration} showActions={false} onReuse={handleReuse} />

		{#if totalPages > 1}
			<div class="flex items-center justify-center mt-4">
				<Pagination {currentPage} {totalPages} size="sm" onPageChange={goToPage} />
			</div>
		{/if}
	{:else}
		<div class="dot-grid text-center py-10 rounded-lg">
			<div class="w-12 h-12 flex items-center justify-center mx-auto mb-3 rounded-lg bg-surface-2 border border-line">
				<Icon name="clock" className="h-6 w-6 text-fg-subtle" strokeWidth={1.5} />
			</div>
			<p class="text-sm text-fg-subtle">No generations yet for this preset</p>
		</div>
	{/if}
</div>

{#if selectedGeneration}
	<GenerationDetailsModal
		generation={selectedGeneration}
		isOpen={true}
		initialFileIndex={selectedFileIndex}
		on:close={handleModalClose}
		on:reuse={(e) => handleReuse(e.detail)}
	/>
{/if}
