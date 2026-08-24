<!--
	Generations that used this model. Appears in both the user and admin model
	details modals, so it owns its own fetch/pagination state rather than
	requiring the parent to load and pass generations down. Mirrors the
	pattern in routes/models/[model_id]/+page.svelte.
-->
<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Spinner } from '$lib/components/ui';
	import JustifiedGenerationGallery from '$lib/components/JustifiedGenerationGallery.svelte';
	import GenerationDetailsModal from '$lib/components/modals/GenerationDetailsModal.svelte';

	export let modelId: string | null | undefined = null;

	const PAGE_SIZE = 20;

	let generations: GenerationHistoryItem[] = [];
	let loading = false;
	let total = 0;
	let page = 1;

	let selectedGeneration: GenerationHistoryItem | null = null;
	let selectedFileIndex = 0;

	let loadedForModelId: string | null = null;

	$: totalPages = Math.ceil(total / PAGE_SIZE);

	$: if (modelId && modelId !== loadedForModelId) {
		loadedForModelId = modelId;
		page = 1;
		loadGenerations();
	}

	async function loadGenerations() {
		if (!modelId) return;
		loading = true;
		try {
			const response = await api.getModelGenerations(modelId, {
				limit: PAGE_SIZE,
				offset: (page - 1) * PAGE_SIZE
			});
			if (response.success && response.data) {
				generations = response.data.generations;
				total = response.data.total;
			}
		} catch (error) {
			logger.error('Failed to load model generations:', error);
		} finally {
			loading = false;
		}
	}

	function goToPage(next: number) {
		if (next < 1 || next > totalPages) return;
		page = next;
		loadGenerations();
	}

	function openGeneration(generation: GenerationHistoryItem, fileIndex: number) {
		selectedGeneration = generation;
		selectedFileIndex = fileIndex;
	}

	function handleModalClose() {
		selectedGeneration = null;
		selectedFileIndex = 0;
	}
</script>

<div>
	<div class="flex items-baseline gap-3 mb-3">
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted whitespace-nowrap">
			Generations
		</span>
		{#if total > 0}
			<span class="font-mono tabular-nums text-2xs text-fg-subtle">{total}</span>
		{/if}
		<div class="flex-1 h-px bg-line self-center"></div>
	</div>

	{#if loading}
		<div class="flex items-center justify-center py-10">
			<Spinner size="md" />
		</div>
	{:else if generations.length > 0}
		<JustifiedGenerationGallery {generations} onOpen={openGeneration} showActions={false} />

		{#if totalPages > 1}
			<div class="flex items-center justify-center gap-4 mt-6">
				<IconButton
					icon="chevron-left"
					label="Previous page"
					variant="secondary"
					disabled={page === 1}
					onclick={() => goToPage(page - 1)}
				/>
				<span class="font-mono tabular-nums text-2xs uppercase tracking-[0.07em] text-fg-muted select-none">
					Page {page} / {totalPages}
				</span>
				<IconButton
					icon="chevron-right"
					label="Next page"
					variant="secondary"
					disabled={page === totalPages}
					onclick={() => goToPage(page + 1)}
				/>
			</div>
		{/if}
	{:else}
		<div class="dot-grid text-center py-10 rounded-lg">
			<div
				class="w-12 h-12 flex items-center justify-center mx-auto mb-3 rounded-lg bg-surface-2 border border-line"
			>
				<Icon name="image" className="h-6 w-6 text-fg-subtle" strokeWidth={1.5} />
			</div>
			<p class="text-sm text-fg-subtle">No generations have used this model yet.</p>
		</div>
	{/if}
</div>

{#if selectedGeneration}
	<GenerationDetailsModal
		generation={selectedGeneration}
		isOpen={true}
		initialFileIndex={selectedFileIndex}
		on:close={handleModalClose}
	/>
{/if}
