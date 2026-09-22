<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, Spinner } from '$lib/components/ui';
	import { PaneRow, PaneSectionLabel } from '$lib/components/pane';
	import type { ChipData, CreateSavedSegmentInput, RichSegment, RichSegmentType, SavedSegment, SegmentCategory } from '$lib/types/segments';
	import { applySegmentList } from '$lib/utils/richSegments';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { tabsStore, activeTab } from '$lib/stores/tabs';
	import LibraryShell from '../library/LibraryShell.svelte';
	import { setLibraryCount } from '../library/libraryCounts';
	import { withSection } from '../library/librarySection';
	import SegmentCard from './SegmentCard.svelte';
	import SegmentDetailView from './SegmentDetailView.svelte';
	import SegmentFiltersPopover from './SegmentFiltersPopover.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import {
		SEGMENT_SORT_OPTIONS,
		applySegmentFilters,
		clearAllSegmentFilters,
		clearSegmentFilterChip,
		segmentCountsByCategory,
		segmentFilterActiveCount,
		segmentFilterChips,
		segmentFiltersFromSearchParams,
		segmentFiltersToSearchParams,
		segmentTagVocabulary,
		type SegmentFilters,
		type SegmentSortBy
	} from './segmentFilters';

	const NAME_FIELD_ID = 'segment-detail-name-field';

	const params = $derived($page.url.searchParams);
	const filters = $derived(segmentFiltersFromSearchParams(params));
	const viewId = $derived(params.get('id'));
	const viewIsNew = $derived(params.get('new') === '1');
	const detailOpen = $derived(!!viewId || viewIsNew);

	let segments = $state<SavedSegment[]>([]);
	let categories = $state<SegmentCategory[]>([]);
	let loading = $state(true);
	let selectedIds = $state(new Set<string>());

	const counts = $derived(segmentCountsByCategory(segments));
	const visible = $derived(applySegmentFilters(segments, categories, filters));
	const chips = $derived(segmentFilterChips(filters, categories));
	const filterCount = $derived(segmentFilterActiveCount(filters));
	const filtersActive = $derived(filterCount > 0 || !!filters.q.trim());
	const tagVocabulary = $derived(segmentTagVocabulary(segments));
	const categoryNames = $derived(new Map(categories.map((category) => [category.id, category.name])));

	function buildUrl(overrides: { filters?: SegmentFilters; id?: string | null; isNew?: boolean } = {}): string {
		const next = segmentFiltersToSearchParams(overrides.filters ?? filters);
		const id = overrides.id !== undefined ? overrides.id : viewId;
		const isNew = overrides.isNew !== undefined ? overrides.isNew : viewIsNew;
		if (id) next.set('id', id);
		if (isNew) next.set('new', '1');
		return `/prompts?${withSection(next, 'segments').toString()}`;
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;

	function updateFilters(next: SegmentFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ filters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function selectCategory(id: string) {
		updateFilters({ ...filters, category: filters.category === id ? '' : id });
	}

	function openSegment(segment: SavedSegment) {
		void goto(buildUrl({ id: segment.id, isNew: false }));
	}

	function backToGrid() {
		void goto(buildUrl({ id: null, isNew: false }));
	}

	function startNew() {
		void goto(buildUrl({ id: null, isNew: true }));
	}

	async function loadAll() {
		loading = true;
		try {
			const [categoryResponse, segmentResponse] = await Promise.all([
				api.listSegmentCategories(),
				api.listSavedSegments()
			]);
			categories = categoryResponse.data?.categories || [];
			segments = segmentResponse.data?.segments || [];
			setLibraryCount('segments', segments.length);
			setLibraryCount('categories', categories.length);
		} catch {
			toasts.error('Failed to load segments');
		} finally {
			loading = false;
		}
	}

	onMount(loadAll);

	function toggleSelect(segment: SavedSegment) {
		const next = new Set(selectedIds);
		if (next.has(segment.id)) next.delete(segment.id);
		else next.add(segment.id);
		selectedIds = next;
	}

	function selectAll() {
		selectedIds = new Set(visible.map((segment) => segment.id));
	}

	function clearSelection() {
		selectedIds = new Set();
	}

	let mode = $state<'create' | 'edit'>('edit');
	let selected = $state<SavedSegment | null>(null);
	let name = $state('');
	let categoryId = $state('');
	let type = $state<RichSegmentType>('content');
	let content = $state('');
	let chipsData = $state<Record<string, ChipData>>({});
	let enabled = $state(true);
	let color = $state('');
	let description = $state('');
	let tagsText = $state('');
	let saving = $state(false);
	let snapshot = $state('');

	function parseTags(value: string): string[] {
		return value
			.split(',')
			.map((tag) => tag.trim())
			.filter(Boolean);
	}

	function buildPayload(): CreateSavedSegmentInput {
		return {
			name: name.trim(),
			category_id: categoryId,
			type,
			content: type === 'break' ? '' : content,
			chips: type === 'break' ? {} : chipsData,
			enabled,
			color: color || null,
			description: description.trim() || null,
			tags: parseTags(tagsText)
		};
	}

	function fieldSnapshot(): Record<string, string> {
		const payload = buildPayload();
		return {
			name: payload.name,
			category: payload.category_id,
			type: payload.type,
			content: JSON.stringify({ content: payload.content, chips: payload.chips }),
			enabled: String(payload.enabled),
			color: payload.color ?? '',
			description: payload.description ?? '',
			tags: payload.tags.join(',')
		};
	}

	const dirtyCount = $derived.by(() => {
		if (!snapshot) return 0;
		const before = JSON.parse(snapshot) as Record<string, string>;
		const after = fieldSnapshot();
		return Object.keys(after).filter((key) => before[key] !== after[key]).length;
	});
	const selectedCategoryCount = $derived(counts.get(categoryId) ?? 0);

	function takeSnapshot() {
		snapshot = JSON.stringify(fieldSnapshot());
	}

	function fillFrom(segment: SavedSegment) {
		name = segment.name;
		categoryId = segment.category_id;
		type = segment.type;
		content = segment.content;
		chipsData = $state.snapshot(segment.chips || {});
		enabled = segment.enabled;
		color = segment.color || '';
		description = segment.description || '';
		tagsText = (segment.tags || []).join(', ');
		takeSnapshot();
	}

	function enterCreate() {
		mode = 'create';
		selected = null;
		name = '';
		const preset = params.get('category');
		categoryId = preset && categories.some((category) => category.id === preset) ? preset : categories[0]?.id || '';
		type = 'content';
		content = '';
		chipsData = {};
		enabled = true;
		color = '';
		description = '';
		tagsText = '';
		takeSnapshot();
		tick().then(() => document.getElementById(NAME_FIELD_ID)?.focus());
	}

	function enterEdit(id: string) {
		mode = 'edit';
		const target = segments.find((segment) => segment.id === id) ?? null;
		if (!target) {
			toasts.error('Segment not found');
			backToGrid();
			return;
		}
		selected = target;
		fillFrom(target);
	}

	let lastRouteKey = '';
	$effect(() => {
		if (loading) return;
		const key = viewId ? `edit:${viewId}` : viewIsNew ? 'new' : 'grid';
		if (key === lastRouteKey) return;
		lastRouteKey = key;
		untrack(() => {
			if (viewId) enterEdit(viewId);
			else if (viewIsNew) enterCreate();
		});
	});

	async function save() {
		if (!name.trim() || !categoryId) return;
		saving = true;
		const payload = buildPayload();
		try {
			const response = selected
				? await api.updateSavedSegment(selected.id, payload)
				: await api.createSavedSegment(payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success(selected ? 'Segment updated' : 'Segment created');
			await loadAll();
			if (mode === 'create') {
				void goto(buildUrl({ id: response.data.id, isNew: false }));
			} else {
				selected = response.data;
				fillFrom(response.data);
			}
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save segment');
		} finally {
			saving = false;
		}
	}

	async function discard() {
		if (mode === 'create') {
			if (dirtyCount > 0) {
				const ok = await confirmDialog({
					title: 'Discard new segment?',
					message: 'This segment has not been saved yet. Discard it?',
					variant: 'danger'
				});
				if (!ok) return;
			}
			backToGrid();
			return;
		}
		if (selected) fillFrom(selected);
	}

	function insertIntoPrompt(segment: RichSegment | SavedSegment) {
		const tab = get(activeTab);
		if (!tab) {
			toasts.error('No active Generate tab to insert this segment into');
			return;
		}
		tabsStore.updateTab(tab.id, {
			promptSegments: applySegmentList(tab.promptSegments ?? [], [segment], 'append')
		});
		toasts.success('Segment inserted into the active Generate tab');
		void goto('/');
	}

	function insertFromDetail() {
		const payload = buildPayload();
		insertIntoPrompt({
			type: payload.type,
			content: payload.content,
			chips: payload.chips,
			enabled: payload.enabled,
			name: payload.name,
			color: color || categories.find((category) => category.id === categoryId)?.color || null,
			description: payload.description
		});
	}

	async function duplicateSegment(segment: SavedSegment) {
		try {
			const response = await api.createSavedSegment({
				name: `${segment.name} copy`,
				category_id: segment.category_id,
				type: segment.type,
				content: segment.content,
				chips: segment.chips,
				enabled: segment.enabled,
				color: segment.color ?? null,
				description: segment.description ?? null,
				tags: segment.tags ?? []
			});
			if (!response.success || !response.data) throw new Error(response.error || 'Duplicate failed');
			toasts.success('Segment duplicated');
			await loadAll();
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to duplicate segment');
		}
	}

	function duplicateFromDetail() {
		if (selected) void duplicateSegment(selected);
	}

	async function afterDelete(ids: string[]) {
		if (viewId && ids.includes(viewId)) backToGrid();
		clearSelection();
		await loadAll();
	}

	async function deleteSegment(segment: SavedSegment) {
		const ok = await confirmDialog({
			title: `Delete “${segment.name}”?`,
			message: "This can't be undone.",
			variant: 'danger'
		});
		if (!ok) return;
		try {
			const response = await api.deleteSavedSegment(segment.id);
			if (!response.success) throw new Error(response.error || 'Delete failed');
			toasts.success('Segment deleted');
			await afterDelete([segment.id]);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to delete segment');
		}
	}

	function deleteFromDetail() {
		if (selected) void deleteSegment(selected);
	}

	async function bulkDelete() {
		const targets = segments.filter((segment) => selectedIds.has(segment.id));
		if (!targets.length) return;
		const ok = await confirmDialog({
			title: `Delete ${targets.length} segment${targets.length === 1 ? '' : 's'}?`,
			message: "This can't be undone.",
			variant: 'danger'
		});
		if (!ok) return;
		const removed: string[] = [];
		for (const segment of targets) {
			try {
				const response = await api.deleteSavedSegment(segment.id);
				if (response.success) removed.push(segment.id);
			} catch {
				continue;
			}
		}
		if (removed.length) toasts.success(`Deleted ${removed.length} segment${removed.length === 1 ? '' : 's'}`);
		if (removed.length < targets.length) toasts.error(`Failed to delete ${targets.length - removed.length} segment${targets.length - removed.length === 1 ? '' : 's'}`);
		await afterDelete(removed);
	}

	let moveMenuOpen = $state(false);
	let moveMenuEl: HTMLDivElement | undefined = $state();
	let moving = $state(false);

	async function bulkMoveTo(targetId: string) {
		moveMenuOpen = false;
		const targets = segments.filter((segment) => selectedIds.has(segment.id) && segment.category_id !== targetId);
		if (!targets.length) {
			clearSelection();
			return;
		}
		moving = true;
		let moved = 0;
		for (const segment of targets) {
			try {
				const response = await api.updateSavedSegment(segment.id, {
					name: segment.name,
					category_id: targetId,
					type: segment.type,
					content: segment.content,
					chips: segment.chips,
					enabled: segment.enabled,
					color: segment.color ?? null,
					description: segment.description ?? null,
					tags: segment.tags ?? []
				});
				if (response.success) moved++;
			} catch {
				continue;
			}
		}
		moving = false;
		if (moved) toasts.success(`Moved ${moved} segment${moved === 1 ? '' : 's'} to ${categoryNames.get(targetId) ?? 'category'}`);
		if (moved < targets.length) toasts.error(`Failed to move ${targets.length - moved} segment${targets.length - moved === 1 ? '' : 's'}`);
		clearSelection();
		await loadAll();
	}

	let gridEl: HTMLDivElement | undefined = $state();

	function handleGridKeydown(event: KeyboardEvent) {
		if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
		const cards = Array.from(gridEl?.querySelectorAll<HTMLElement>('[data-segment-card]') ?? []);
		const currentIndex = cards.indexOf(document.activeElement as HTMLElement);
		if (currentIndex === -1) return;
		event.preventDefault();
		const columns = columnsInFirstRow(cards);
		let nextIndex = currentIndex;
		if (event.key === 'ArrowRight') nextIndex = Math.min(cards.length - 1, currentIndex + 1);
		else if (event.key === 'ArrowLeft') nextIndex = Math.max(0, currentIndex - 1);
		else if (event.key === 'ArrowDown') nextIndex = Math.min(cards.length - 1, currentIndex + columns);
		else if (event.key === 'ArrowUp') nextIndex = Math.max(0, currentIndex - columns);
		cards[nextIndex]?.focus();
	}

	function columnsInFirstRow(cards: HTMLElement[]): number {
		if (cards.length < 2) return 1;
		const firstTop = cards[0].offsetTop;
		let count = 1;
		for (let i = 1; i < cards.length; i++) {
			if (cards[i].offsetTop !== firstTop) break;
			count++;
		}
		return count;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape') return;
		if (moveMenuOpen) moveMenuOpen = false;
		else if (selectedIds.size > 0) clearSelection();
	}

	function handleWindowClick(event: MouseEvent) {
		if (moveMenuOpen && moveMenuEl && !moveMenuEl.contains(event.target as Node)) moveMenuOpen = false;
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} onclick={handleWindowClick} />

<LibraryShell
	section="segments"
	count={visible.length}
	{detailOpen}
	q={filters.q}
	onQueryChange={(value) => updateFilters({ ...filters, q: value })}
	sortBy={filters.sortBy}
	sortOptions={SEGMENT_SORT_OPTIONS}
	onSortChange={(value) => updateFilters({ ...filters, sortBy: value as SegmentSortBy })}
	{filterCount}
	{chips}
	onRemoveChip={(key) => updateFilters(clearSegmentFilterChip(filters, key))}
	onClearFilters={() => updateFilters(clearAllSegmentFilters(filters))}
	loadedCount={visible.length}
	total={segments.length}
>
	{#snippet sidebarTree()}
		<div class="p-2" role="listbox" aria-label="Segment categories">
			<PaneSectionLabel label="Categories" />
			<div class="mt-1 space-y-0.5">
				<PaneRow
					size="sm"
					icon="list"
					title="All segments"
					count={segments.length}
					selected={!filters.category}
					onclick={() => updateFilters({ ...filters, category: '' })}
				/>
				{#each categories as category (category.id)}
					<PaneRow
						size="sm"
						dot={category.color}
						title={category.name}
						count={counts.get(category.id) ?? 0}
						selected={filters.category === category.id}
						onclick={() => selectCategory(category.id)}
					/>
				{/each}
			</div>
		</div>
	{/snippet}

	{#snippet filtersPopover(close)}
		<SegmentFiltersPopover {filters} {categories} tags={tagVocabulary} onChange={updateFilters} onClose={close} />
	{/snippet}

	{#snippet primary()}
		<Button size="sm" variant="primary" icon="plus" onclick={startNew}>New segment</Button>
	{/snippet}

	{#if detailOpen}
		<SegmentDetailView
			{mode}
			segment={selected}
			{categories}
			categorySegmentCount={selectedCategoryCount}
			bind:name
			bind:categoryId
			bind:type
			bind:content
			bind:chips={chipsData}
			bind:enabled
			bind:color
			bind:description
			bind:tagsText
			{saving}
			{dirtyCount}
			onBack={backToGrid}
			onSave={save}
			onDiscard={discard}
			onDelete={deleteFromDetail}
			onDuplicate={duplicateFromDetail}
			onInsert={insertFromDetail}
		/>
	{:else}
		<div class="flex h-full flex-col overflow-hidden">
			<div class="min-h-0 flex-1 overflow-y-auto p-4">
				{#if loading}
					<div class="flex h-40 items-center justify-center">
						<Spinner size="lg" />
					</div>
				{:else if visible.length === 0}
					<div class="flex h-full items-center justify-center">
						<EmptyState
							icon="list"
							title={filtersActive ? 'No segments match these filters' : 'No segments yet'}
							description={filtersActive
								? 'Try clearing a filter or broadening the search.'
								: 'Save reusable prompt fragments to insert into any prompt.'}
						>
							{#snippet actions()}
								{#if filtersActive}
									<Button size="sm" variant="ghost" onclick={() => updateFilters({ ...clearAllSegmentFilters(filters), q: '' })}>
										Clear filters
									</Button>
								{:else}
									<Button size="sm" variant="primary" icon="plus" onclick={startNew}>Create a segment</Button>
								{/if}
							{/snippet}
						</EmptyState>
					</div>
				{:else}
					<div
						bind:this={gridEl}
						class="grid grid-cols-[repeat(auto-fill,minmax(360px,1fr))] gap-3"
						role="toolbar"
						aria-label="Segment cards"
						aria-orientation="horizontal"
						tabindex="-1"
						onkeydown={handleGridKeydown}
					>
						{#each visible as segment (segment.id)}
							<SegmentCard
								{segment}
								categoryName={categoryNames.get(segment.category_id) ?? null}
								selected={selectedIds.has(segment.id)}
								onToggleSelect={toggleSelect}
								onOpen={openSegment}
								onInsert={insertIntoPrompt}
								onDuplicate={duplicateSegment}
								onDelete={deleteSegment}
							/>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	{/if}
</LibraryShell>

<SelectionActionBar
	active={selectedIds.size > 0}
	selectedCount={selectedIds.size}
	totalCount={visible.length}
	onSelectAll={selectAll}
	onClearSelection={clearSelection}
	onClose={clearSelection}
	feedback={null}
>
	<svelte:fragment slot="actionsAfterCollection">
		<div class="relative" bind:this={moveMenuEl}>
			<button
				type="button"
				class="flex items-center gap-1.5 rounded px-3 py-1.5 text-sm text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:opacity-50"
				aria-haspopup="menu"
				aria-expanded={moveMenuOpen}
				disabled={moving || categories.length === 0}
				onclick={() => (moveMenuOpen = !moveMenuOpen)}
			>
				<Icon name="folder" className="h-4 w-4" />
				Move to category…
				<Icon name="chevron-up" className="h-3 w-3" />
			</button>
			{#if moveMenuOpen}
				<div
					class="absolute bottom-[calc(100%+6px)] left-0 z-50 max-h-64 min-w-[200px] overflow-y-auto rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating"
					role="menu"
				>
					{#each categories as category (category.id)}
						<button
							type="button"
							role="menuitem"
							class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
							onclick={() => bulkMoveTo(category.id)}
						>
							<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" style="background: {category.color}"></span>
							<span class="min-w-0 flex-1 truncate">{category.name}</span>
							<span class="font-mono text-xs tabular-nums text-fg-subtle">{counts.get(category.id) ?? 0}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>
		<button
			type="button"
			class="flex items-center gap-2 rounded bg-danger-solid px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-danger-solid/90"
			onclick={bulkDelete}
		>
			<Icon name="trash" className="h-4 w-4" />
			Delete
		</button>
	</svelte:fragment>
</SelectionActionBar>
