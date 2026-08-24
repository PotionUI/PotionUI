<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { api } from '$lib/services/api';
	import type { SavedSegment, SegmentCategory } from '$lib/types/segments';
	import BaseModal from './BaseModal.svelte';
	import { Button, Spinner, Alert } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	export let isOpen = false;
	export let title = 'Replace from saved Segment';
	const dispatch = createEventDispatcher<{
		close: void;
		select: { savedSegment: SavedSegment; category?: SegmentCategory };
	}>();

	let segments: SavedSegment[] = [];
	let categories: SegmentCategory[] = [];
	let categoryId = '';
	let search = '';
	let loading = false;
	let error = '';
	let previousOpen = false;

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) load();
	}
	$: query = search.trim().toLowerCase();
	$: filtered = segments.filter((segment) =>
		(!categoryId || segment.category_id === categoryId) &&
		(!query || [segment.name, segment.content, segment.description || '', ...(segment.tags || [])]
			.join(' ').toLowerCase().includes(query))
	);

	async function load() {
		loading = true;
		error = '';
		try {
			const [segmentResponse, categoryResponse] = await Promise.all([
				api.listSavedSegments(), api.listSegmentCategories()
			]);
			segments = segmentResponse.data?.segments || [];
			categories = categoryResponse.data?.categories || [];
		} catch (loadError) {
			error = loadError instanceof Error ? loadError.message : 'Failed to load saved Segments';
		} finally {
			loading = false;
		}
	}

	function choose(segment: SavedSegment) {
		dispatch('select', {
			savedSegment: segment,
			category: categories.find((category) => category.id === segment.category_id)
		});
	}
</script>

<BaseModal {isOpen} {title} sizeClass="md:max-w-2xl md:w-full md:max-h-[85vh]" on:close={() => dispatch('close')}>
	<svelte:fragment slot="headerIcon"><Icon name="book-open" className="h-5 w-5 text-fg-muted" /></svelte:fragment>
	<div class="space-y-3 p-4 sm:p-6">
		<div class="grid gap-2 sm:grid-cols-[1fr_12rem]">
			<input class="input w-full" type="search" bind:value={search} placeholder="Search saved Segments…" />
			<select class="input w-full" bind:value={categoryId}>
				<option value="">All categories</option>
				{#each categories as category}<option value={category.id}>{category.name}</option>{/each}
			</select>
		</div>
		{#if loading}<div class="flex justify-center py-10"><Spinner size="lg" /></div>
		{:else if error}<Alert variant="danger" density="compact" live="polite">{error}</Alert>
		{:else if filtered.length === 0}<div class="rounded-lg border border-dashed border-line p-8 text-center text-sm text-fg-muted">No saved Segments found.</div>
		{:else}
			<div class="max-h-[50vh] space-y-1 overflow-y-auto">
				{#each filtered as segment (segment.id)}
					<button type="button" class="flex w-full items-start gap-3 rounded-lg border border-line bg-surface-2 p-3 text-left hover:border-line-hover hover:bg-surface-3" on:click={() => choose(segment)}>
						<span class="mt-1 h-2.5 w-2.5 flex-shrink-0 rounded-full" style={`background:${segment.effective_color || '#3B82F6'}`}></span>
						<span class="min-w-0 flex-1"><span class="block truncate text-sm font-medium">{segment.name}</span><span class="mt-1 block line-clamp-2 text-xs text-fg-muted">{segment.type === 'break' ? 'Prompt break' : segment.content || 'Blank starter content'}</span></span>
					</button>
				{/each}
			</div>
		{/if}
	</div>
	<svelte:fragment slot="footer"><div class="flex justify-end px-4 py-3 sm:px-6"><Button variant="secondary" onclick={() => dispatch('close')}>Cancel</Button></div></svelte:fragment>
</BaseModal>
