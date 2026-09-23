<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api';
	import Icon from '$lib/components/Icon.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, EmptyState, Spinner } from '$lib/components/ui';
	import type { SavedSegment, SegmentCategory } from '$lib/types/segments';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import { libraryCounts, setLibraryCount } from '../library/libraryCounts';
	import { LIBRARY_SECTIONS, sectionHref, withSection } from '../library/librarySection';
	import CategoryCard from './CategoryCard.svelte';
	import CategoryDetailView from './CategoryDetailView.svelte';
	import CategoryFiltersPopover from './CategoryFiltersPopover.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { segmentCountsByCategory } from './segmentFilters';
	import {
		CATEGORY_SORT_OPTIONS,
		applyCategoryFilters,
		categoryFilterActiveCount,
		categoryFilterChips,
		categoryFiltersFromSearchParams,
		categoryFiltersToSearchParams,
		clearAllCategoryFilters,
		clearCategoryFilterChip,
		type CategoryFilters,
		type CategorySortBy
	} from './categoryFilters';

	const DEFAULT_COLOR = '#3B82F6';

	const params = $derived($page.url.searchParams);
	const filters = $derived(categoryFiltersFromSearchParams(params));
	const viewId = $derived(params.get('id'));
	const viewIsNew = $derived(params.get('new') === '1');
	const detailOpen = $derived(!!viewId || viewIsNew);

	let categories = $state<SegmentCategory[]>([]);
	let segments = $state<SavedSegment[]>([]);
	let loading = $state(true);
	let selectedIds = $state(new Set<string>());

	const counts = $derived(segmentCountsByCategory(segments));
	const visible = $derived(applyCategoryFilters(categories, counts, filters));
	const chips = $derived(categoryFilterChips(filters));
	const filterCount = $derived(categoryFilterActiveCount(filters));
	const filtersActive = $derived(filterCount > 0 || !!filters.q.trim());

	function buildUrl(overrides: { filters?: CategoryFilters; id?: string | null; isNew?: boolean } = {}): string {
		const next = categoryFiltersToSearchParams(overrides.filters ?? filters);
		const id = overrides.id !== undefined ? overrides.id : viewId;
		const isNew = overrides.isNew !== undefined ? overrides.isNew : viewIsNew;
		if (id) next.set('id', id);
		if (isNew) next.set('new', '1');
		return `/prompts?${withSection(next, 'categories').toString()}`;
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;

	function updateFilters(next: CategoryFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ filters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function openCategory(category: SegmentCategory) {
		void goto(buildUrl({ id: category.id, isNew: false }));
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
			setLibraryCount('categories', categories.length);
			setLibraryCount('segments', segments.length);
		} catch {
			toasts.error('Failed to load categories');
		} finally {
			loading = false;
		}
	}

	onMount(loadAll);

	function toggleSelect(category: SegmentCategory) {
		const next = new Set(selectedIds);
		if (next.has(category.id)) next.delete(category.id);
		else next.add(category.id);
		selectedIds = next;
	}

	function selectAll() {
		selectedIds = new Set(visible.map((category) => category.id));
	}

	function clearSelection() {
		selectedIds = new Set();
	}

	let mode = $state<'create' | 'edit'>('edit');
	let selected = $state<SegmentCategory | null>(null);
	let name = $state('');
	let color = $state(DEFAULT_COLOR);
	let description = $state('');
	let saving = $state(false);
	let snapshot = $state({ name: '', color: DEFAULT_COLOR, description: '' });

	const dirtyCount = $derived.by(() => {
		let count = 0;
		if (name.trim() !== snapshot.name) count++;
		if (color !== snapshot.color) count++;
		if (description.trim() !== snapshot.description) count++;
		return count;
	});
	const selectedSegments = $derived.by(() => {
		const current = selected;
		return current ? segments.filter((segment) => segment.category_id === current.id) : [];
	});

	function takeSnapshot() {
		snapshot = { name: name.trim(), color, description: description.trim() };
	}

	function fillFrom(category: SegmentCategory) {
		name = category.name;
		color = category.color;
		description = category.description || '';
		takeSnapshot();
	}

	function enterCreate() {
		mode = 'create';
		selected = null;
		name = '';
		color = DEFAULT_COLOR;
		description = '';
		takeSnapshot();
	}

	function enterEdit(id: string) {
		mode = 'edit';
		const target = categories.find((category) => category.id === id) ?? null;
		if (!target) {
			toasts.error('Category not found');
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
		if (!name.trim()) return;
		saving = true;
		const payload = { name: name.trim(), description: description.trim(), color };
		try {
			const response = selected
				? await api.updateSegmentCategory(selected.id, payload)
				: await api.createSegmentCategory(payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success(selected ? 'Category updated' : 'Category created');
			await loadAll();
			if (mode === 'create') {
				void goto(buildUrl({ id: response.data.id, isNew: false }));
			} else {
				selected = response.data;
				fillFrom(response.data);
			}
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save category');
		} finally {
			saving = false;
		}
	}

	async function discard() {
		if (mode === 'create') {
			if (name.trim()) {
				const ok = await confirmDialog({
					title: 'Discard new category?',
					message: 'This category has not been saved yet. Discard it?',
					variant: 'danger'
				});
				if (!ok) return;
			}
			backToGrid();
			return;
		}
		if (selected) fillFrom(selected);
	}

	let moveTarget = $state<SegmentCategory | null>(null);
	let moveTargetId = $state('');
	let moveBusy = $state(false);
	let moveResolve: ((ok: boolean) => void) | null = null;

	const moveSegments = $derived.by(() => {
		const target = moveTarget;
		return target ? segments.filter((segment) => segment.category_id === target.id) : [];
	});
	const moveOptions = $derived.by(() => {
		const target = moveTarget;
		return target ? categories.filter((category) => category.id !== target.id) : [];
	});

	function askMove(category: SegmentCategory): Promise<boolean> {
		moveTarget = category;
		moveTargetId = categories.find((entry) => entry.id !== category.id)?.id ?? '';
		return new Promise((resolve) => {
			moveResolve = resolve;
		});
	}

	function settleMove(ok: boolean) {
		moveResolve?.(ok);
		moveResolve = null;
		moveTarget = null;
	}

	async function moveSegmentsTo(list: SavedSegment[], targetId: string) {
		for (const segment of list) {
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
			if (!response.success) throw new Error(response.error || `Failed to move “${segment.name}”`);
		}
	}

	async function confirmMoveAndDelete() {
		if (!moveTarget || !moveTargetId) return;
		moveBusy = true;
		try {
			await moveSegmentsTo(moveSegments, moveTargetId);
			await loadAll();
			settleMove(true);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to move segments');
			settleMove(false);
		} finally {
			moveBusy = false;
		}
	}

	async function deleteCategory(category: SegmentCategory, confirmed = false): Promise<boolean> {
		const count = counts.get(category.id) ?? 0;
		const ok = count > 0
			? await askMove(category)
			: confirmed || (await confirmDialog({ title: `Delete “${category.name}”?`, message: "This can't be undone.", variant: 'danger' }));
		if (!ok) return false;
		try {
			const response = await api.deleteSegmentCategory(category.id);
			if (!response.success) throw new Error(response.error || 'Delete failed');
			return true;
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to delete category');
			return false;
		}
	}

	async function afterDelete(ids: string[]) {
		if (viewId && ids.includes(viewId)) backToGrid();
		clearSelection();
		await loadAll();
	}

	async function deleteOne(category: SegmentCategory) {
		if (!(await deleteCategory(category))) return;
		toasts.success('Category deleted');
		await afterDelete([category.id]);
	}

	function deleteFromDetail() {
		if (selected) void deleteOne(selected);
	}

	async function bulkDelete() {
		const targets = categories.filter((category) => selectedIds.has(category.id));
		if (!targets.length) return;
		const ok = await confirmDialog({
			title: `Delete ${targets.length} categor${targets.length === 1 ? 'y' : 'ies'}?`,
			message: "This can't be undone.",
			variant: 'danger'
		});
		if (!ok) return;
		const removed: string[] = [];
		for (const category of targets) {
			if (await deleteCategory(category, true)) removed.push(category.id);
		}
		if (removed.length) toasts.success(`Deleted ${removed.length} categor${removed.length === 1 ? 'y' : 'ies'}`);
		await afterDelete(removed);
	}

	function newSegmentIn(category: SegmentCategory) {
		void goto(sectionHref('segments', { new: '1', category: category.id }));
	}

	let gridEl: HTMLDivElement | undefined = $state();

	function handleGridKeydown(event: KeyboardEvent) {
		if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
		const cards = Array.from(gridEl?.querySelectorAll<HTMLElement>('[data-category-card]') ?? []);
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
		if (event.key === 'Escape' && selectedIds.size > 0 && !moveTarget) clearSelection();
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<LibraryShell
	title="Prompt Library"
	persistKey="prompt-library"
	sections={LIBRARY_SECTIONS}
	section="categories"
	onSelectSection={(id) => goto(sectionHref(id))}
	sectionCounts={$libraryCounts}
	count={visible.length}
	{detailOpen}
	filterChips={chips}
	onRemoveChip={(key) => updateFilters(clearCategoryFilterChip(filters, key))}
	onClearFilters={() => updateFilters(clearAllCategoryFilters(filters))}
	loadedCount={visible.length}
	total={categories.length}
>
	{#snippet toolbar()}
		<LibraryFilterBar
			q={filters.q}
			onQueryChange={(value) => updateFilters({ ...filters, q: value })}
			searchPlaceholder="Search categories…"
			sortBy={filters.sortBy}
			sortOptions={CATEGORY_SORT_OPTIONS}
			onSortChange={(value) => updateFilters({ ...filters, sortBy: value as CategorySortBy })}
			{filterCount}
		>
			{#snippet popover(close)}
				<CategoryFiltersPopover {filters} onChange={updateFilters} onClose={close} />
			{/snippet}
		</LibraryFilterBar>
	{/snippet}

	{#snippet primary()}
		<Button size="sm" variant="primary" icon="plus" onclick={startNew}>New category</Button>
	{/snippet}

	{#if detailOpen}
		<CategoryDetailView
			{mode}
			category={selected}
			bind:name
			bind:color
			bind:description
			segments={selectedSegments}
			{saving}
			{dirtyCount}
			onBack={backToGrid}
			onSave={save}
			onDiscard={discard}
			onDelete={deleteFromDetail}
			newSegmentHref={sectionHref('segments', selected ? { new: '1', category: selected.id } : { new: '1' })}
			filteredSegmentsHref={sectionHref('segments', selected ? { category: selected.id } : {})}
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
							icon="folder"
							title={filtersActive ? 'No categories match these filters' : 'No categories yet'}
							description={filtersActive
								? 'Try clearing a filter or broadening the search.'
								: 'Create a category to group your segments.'}
						>
							{#snippet actions()}
								{#if filtersActive}
									<Button size="sm" variant="ghost" onclick={() => updateFilters({ ...clearAllCategoryFilters(filters), q: '' })}>
										Clear filters
									</Button>
								{:else}
									<Button size="sm" variant="primary" icon="plus" onclick={startNew}>Create a category</Button>
								{/if}
							{/snippet}
						</EmptyState>
					</div>
				{:else}
					<div
						bind:this={gridEl}
						class="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3"
						role="toolbar"
						aria-label="Category cards"
						aria-orientation="horizontal"
						tabindex="-1"
						onkeydown={handleGridKeydown}
					>
						{#each visible as category (category.id)}
							<CategoryCard
								{category}
								count={counts.get(category.id) ?? 0}
								selected={selectedIds.has(category.id)}
								onToggleSelect={toggleSelect}
								onOpen={openCategory}
								onNewSegment={newSegmentIn}
								onDelete={deleteOne}
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

{#if moveTarget}
	<BaseModal isOpen={true} title={`Delete “${moveTarget.name}”?`} size="sm" on:close={() => settleMove(false)}>
		<div class="space-y-3 px-6 py-4">
			<p class="text-sm text-fg-muted">
				<span class="font-mono tabular-nums text-fg">{moveSegments.length}</span>
				segment{moveSegments.length === 1 ? ' is' : 's are'} in this category. Move
				{moveSegments.length === 1 ? 'it' : 'them'} to another category before deleting.
			</p>
			{#if moveOptions.length === 0}
				<p class="text-xs text-warning">Create another category first.</p>
			{:else}
				<label>
					<span class="mb-1.5 block text-xs font-medium text-fg-muted">Move to</span>
					<select class="input text-sm" bind:value={moveTargetId}>
						{#each moveOptions as option (option.id)}
							<option value={option.id}>{option.name}</option>
						{/each}
					</select>
				</label>
			{/if}
		</div>
		<svelte:fragment slot="footer">
			<div class="flex justify-end gap-2 px-6 py-4">
				<Button size="sm" variant="secondary" disabled={moveBusy} onclick={() => settleMove(false)}>Cancel</Button>
				<Button
					size="sm"
					variant="danger"
					loading={moveBusy}
					disabled={moveOptions.length === 0 || !moveTargetId}
					onclick={confirmMoveAndDelete}
				>
					Move {moveSegments.length} segment{moveSegments.length === 1 ? '' : 's'} and delete
				</Button>
			</div>
		</svelte:fragment>
	</BaseModal>
{/if}
