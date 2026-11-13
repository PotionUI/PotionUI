<script lang="ts">
	import { createEventDispatcher, tick } from 'svelte';
	import { api } from '$lib/services/api';
	import type { SavedSegment, SegmentCategory } from '$lib/types/segments';
	import SegmentModal from './SegmentModal.svelte';
	import { Badge, Spinner, Alert, Button, EmptyState } from '$lib/components/ui';
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
	let selectedId: string | null = null;
	let listEl: HTMLDivElement | undefined;

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) {
			search = '';
			categoryId = '';
			selectedId = null;
			load();
		}
	}
	$: query = search.trim().toLowerCase();
	$: filtered = segments.filter((segment) =>
		(!categoryId || segment.category_id === categoryId) &&
		(!query || [segment.name, segment.content, segment.description || '', ...(segment.tags || [])]
			.join(' ').toLowerCase().includes(query))
	);
	$: if (filtered.length > 0 && !filtered.some((segment) => segment.id === selectedId)) selectedId = filtered[0].id;
	$: selected = filtered.find((segment) => segment.id === selectedId) ?? null;
	$: action = title.startsWith('Insert') ? 'Insert' : 'Replace';
	$: categoryOf = (segment: SavedSegment) => categories.find((category) => category.id === segment.category_id);

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

	function choose(segment: SavedSegment | null = selected) {
		if (!segment) return false;
		dispatch('select', { savedSegment: segment, category: categoryOf(segment) });
	}

	function move(step: 1 | -1) {
		if (!filtered.length) return;
		const index = filtered.findIndex((segment) => segment.id === selectedId);
		selectedId = filtered[Math.min(Math.max(index + step, 0), filtered.length - 1)].id;
		void tick().then(() => listEl?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest' }));
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		event.preventDefault();
		move(event.key === 'ArrowDown' ? 1 : -1);
	}
</script>

<SegmentModal
	{isOpen}
	tier="L"
	icon="book-open"
	{title}
	context={`${segments.length} saved Segment${segments.length === 1 ? '' : 's'}`}
	primaryLabel={action}
	primaryDisabled={!selected}
	onConfirm={() => choose()}
	onCancel={() => dispatch('close')}
>
	<div class="flex h-full min-h-0 flex-col">
		<div class="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3 max-md:px-4">
			<div class="input flex h-9 min-w-[14rem] flex-1 items-center gap-2 max-md:h-11 max-md:basis-full">
				<Icon name="search" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
				<input
					class="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-fg-subtle"
					type="search"
					bind:value={search}
					placeholder="Search saved Segments…"
					aria-label="Search saved Segments"
					data-autofocus
					on:keydown={handleKeydown}
				/>
				<span class="whitespace-nowrap font-mono text-sm tabular-nums text-fg-subtle" data-testid="saved-segment-count">
					{query || categoryId ? `${filtered.length} of ${segments.length}` : segments.length}
				</span>
			</div>
			<select class="input h-9 w-48 text-sm max-md:h-11 max-md:flex-1" bind:value={categoryId} aria-label="Category">
				<option value="">All categories</option>
				{#each categories as category (category.id)}<option value={category.id}>{category.name}</option>{/each}
			</select>
		</div>
		<div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_22.5rem] max-md:grid-cols-1">
			<div class="min-h-0 overflow-y-auto p-2">
				{#if loading}
					<div class="flex justify-center py-10"><Spinner size="lg" /></div>
				{:else if error}
					<div class="p-2"><Alert variant="danger" density="compact" live="polite">{error}</Alert></div>
				{:else if filtered.length === 0}
					<div data-testid="saved-segment-empty">
						{#if segments.length === 0}
							<EmptyState compact icon="document" title="No saved Segments yet" />
						{:else}
							<EmptyState compact icon="search" title="No saved Segments match" description={query ? `“${search.trim()}”` : undefined}>
								{#snippet actions()}
									<Button
										variant="secondary"
										size="sm"
										onclick={() => {
											search = '';
											categoryId = '';
										}}>Clear search</Button
									>
								{/snippet}
							</EmptyState>
						{/if}
					</div>
				{:else}
					<div class="space-y-0.5" role="listbox" tabindex="-1" aria-label="Saved Segments" bind:this={listEl} on:keydown={handleKeydown}>
						{#each filtered as segment (segment.id)}
							{@const isSelected = segment.id === selectedId}
							<button
								type="button"
								role="option"
								aria-selected={isSelected}
								data-saved-segment={segment.id}
								class="grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3 rounded px-2.5 py-2.5 text-left transition-colors {isSelected
									? 'bg-signal/10 shadow-[inset_2px_0_0_rgb(var(--signal))]'
									: 'hover:bg-surface-2'}"
								on:click={() => (selectedId = segment.id)}
								on:dblclick={() => choose(segment)}
							>
								<span
									class="mt-1.5 h-2.5 w-2.5 flex-shrink-0 rounded-full {segment.effective_color ? '' : 'bg-line-strong'}"
									style={segment.effective_color ? `background-color: ${segment.effective_color};` : undefined}
									data-testid="saved-segment-dot"
								></span>
								<span class="min-w-0">
									<span class="block truncate text-base font-medium text-fg">{segment.name}</span>
									<span class="mt-0.5 block truncate text-sm text-fg-subtle">
										{categoryOf(segment)?.name ?? 'Uncategorized'} · {segment.content || 'Blank starter content'}
									</span>
								</span>
								<span
									class="mt-0.5 grid h-[18px] w-[18px] flex-shrink-0 place-items-center rounded-full border {isSelected
										? 'border-signal bg-signal text-accent-contrast'
										: 'border-line-hover text-transparent'}"
									aria-hidden="true"
								>
									<Icon name="check" className="h-3 w-3" />
								</span>
							</button>
						{/each}
					</div>
				{/if}
			</div>
			<aside class="min-h-0 overflow-y-auto border-l border-line bg-surface-2/30 px-5 py-4 max-md:hidden" aria-label="Preview">
				{#if selected}
					<span class="font-mono text-sm uppercase tracking-[0.04em] text-fg-subtle">Preview</span>
					<h3 class="mt-1 break-words text-md font-semibold text-fg">{selected.name}</h3>
					<p class="mt-0.5 text-sm text-fg-subtle">{categoryOf(selected)?.name ?? 'Uncategorized'}</p>
					{#if selected.description}
						<p class="mt-2 text-sm text-fg-muted">{selected.description}</p>
					{/if}
					<div class="mt-3 whitespace-pre-wrap break-words rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm leading-5 text-fg-muted">
						{selected.content || 'Blank starter content'}
					</div>
					{#if selected.tags?.length}
						<div class="mt-3 flex flex-wrap gap-1.5">
							{#each selected.tags as tag (tag)}<Badge size="lg">{tag}</Badge>{/each}
						</div>
					{/if}
				{:else}
					<p class="py-10 text-center text-base text-fg-subtle">Pick one on the left to see its content.</p>
				{/if}
			</aside>
		</div>
	</div>
</SegmentModal>
