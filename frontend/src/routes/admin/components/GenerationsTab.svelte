<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import * as adminApi from '$lib/services/admin-api';
	import type { AdminGenerationDetailResult, AdminGenerationListItem } from '$lib/services/admin-api';
	import type { User } from '$lib/stores/auth';
	import { debounce } from '$lib/stores/tabPersistence';
	import { timeAgo, parseServerDate } from '$lib/utils/relativeTime';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { logger } from '$lib/utils/logger';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import { Button, EmptyState, Spinner } from '$lib/components/ui';
	import { DataTable, StatusCell, TablePager, pageCount, type SortState } from '$lib/components/table';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import GenerationRunReport from './GenerationRunReport.svelte';
	import GenerationsFiltersPopover from './GenerationsFiltersPopover.svelte';
	import { GENERATION_LIBRARY_SECTIONS, sectionFromStatus, statusFromSection, type GenerationSection } from './generations/generationsSections';
	import { durationFor, presetTitleFor, sortByFromSortState, sortStateFromSortBy } from './generations/generationsColumns';
	import {
		GENERATION_SORT_OPTIONS,
		clearAllGenerationsFilters,
		clearGenerationsFilterChip,
		generationsFilterActiveCount,
		generationsFilterChips,
		generationsFiltersFromSearchParams,
		generationsFiltersToSearchParams,
		generationsSortParams,
		type GenerationSortBy,
		type GenerationsFilters
	} from './generationsFilters';

	const FILTER_PARAM_KEYS = ['q', 'status', 'user', 'from', 'to', 'sort_by'];

	const STATUS_TONE: Record<string, 'success' | 'info' | 'muted' | 'danger' | 'warning'> = {
		completed: 'success',
		running: 'info',
		pending: 'muted',
		failed: 'danger',
		cancelled: 'warning'
	};

	let generations = $state<AdminGenerationListItem[]>([]);
	let total = $state(0);
	let pageIndex = $state(1);
	let pageSize = $state(25);
	let listLoading = $state(true);
	let listError = $state<string | null>(null);
	let lastFiltersKey = '';

	let users = $state<User[]>([]);
	let usersById = $derived(new Map(users.map((u) => [u.id, u])));

	let selected = $state<Set<string>>(new Set());
	let bulkDeleting = $state(false);

	let detail = $state<AdminGenerationDetailResult | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);
	let detailRequestVersion = 0;
	let lastViewId: string | null = null;

	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);

	const filters = $derived(generationsFiltersFromSearchParams($page.url.searchParams));
	const filtersKey = $derived(JSON.stringify(filters));
	const activeFilterCount = $derived(generationsFilterActiveCount(filters));
	const chips = $derived(generationsFilterChips(filters, (userId) => usersById.get(userId)?.username ?? userId));
	const section = $derived(sectionFromStatus(filters.status));
	const sort = $derived(sortStateFromSortBy(filters.sortBy));

	let sectionCountsCache = $state<Partial<Record<GenerationSection, number>>>({});

	$effect(() => {
		if (filtersKey !== lastFiltersKey) {
			lastFiltersKey = filtersKey;
			pageIndex = 1;
			void loadGenerations();
		}
	});

	$effect(() => {
		if (viewId === untrack(() => lastViewId)) return;
		lastViewId = viewId;
		untrack(() => {
			detail = null;
			detailError = null;
			selected = new Set();
			if (viewId) void loadDetail(viewId);
		});
	});

	onMount(async () => {
		const usersResponse = await adminApi.getUsers();
		if (usersResponse.success && usersResponse.data) users = usersResponse.data;
	});

	async function loadGenerations() {
		listLoading = true;
		listError = null;
		try {
			const sortParams = generationsSortParams(filters.sortBy);
			const response = await adminApi.getAdminGenerations({
				limit: pageSize,
				offset: (pageIndex - 1) * pageSize,
				status: filters.status || undefined,
				userId: filters.userId || undefined,
				search: filters.q || undefined,
				createdFrom: filters.createdFrom || undefined,
				createdTo: filters.createdTo || undefined,
				sortBy: sortParams.sortBy,
				sortDir: sortParams.sortDir
			});
			if (response.success && response.data) {
				generations = response.data.generations;
				total = response.data.total;
				if (!filters.q && !filters.userId && !filters.createdFrom && !filters.createdTo) {
					sectionCountsCache = { ...sectionCountsCache, [section]: total };
				}
			} else {
				listError = response.message || 'Failed to load generations';
			}
		} catch (e: any) {
			listError = e?.response?.data?.message || e?.message || 'Failed to load generations';
		} finally {
			listLoading = false;
		}
	}

	async function loadDetail(id: string) {
		const version = ++detailRequestVersion;
		detailLoading = true;
		detailError = null;
		try {
			const response = await adminApi.getAdminGenerationDetail(id);
			if (version !== detailRequestVersion || id !== viewId) return;
			if (response.success && response.data) {
				detail = response.data;
			} else {
				detailError = response.message || 'Failed to load generation detail';
			}
		} catch (e: any) {
			if (version !== detailRequestVersion || id !== viewId) return;
			detailError = e?.response?.data?.message || e?.message || 'Failed to load generation detail';
		} finally {
			if (version === detailRequestVersion && id === viewId) detailLoading = false;
		}
	}

	function usernameFor(userId: string): string {
		return usersById.get(userId)?.username ?? userId;
	}

	function absoluteTime(iso: string | undefined): string {
		if (!iso) return '—';
		const date = parseServerDate(iso);
		if (!date) return '—';
		return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
	}

	function applyFilters(next: GenerationsFilters) {
		const url = new URL($page.url);
		for (const key of FILTER_PARAM_KEYS) url.searchParams.delete(key);
		for (const [key, value] of generationsFiltersToSearchParams(next)) url.searchParams.set(key, value);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const debouncedApplyFilters = debounce(applyFilters, 300);

	function onQueryChange(value: string) {
		debouncedApplyFilters({ ...filters, q: value });
	}

	function onSortChange(value: string) {
		applyFilters({ ...filters, sortBy: value as GenerationSortBy });
	}

	function onTableSortChange(next: SortState | null) {
		applyFilters({ ...filters, sortBy: sortByFromSortState(next) });
	}

	function onPopoverChange(next: GenerationsFilters) {
		applyFilters(next);
	}

	function onRemoveChip(key: string) {
		applyFilters(clearGenerationsFilterChip(filters, key));
	}

	function onClearAllFilters() {
		applyFilters(clearAllGenerationsFilters(filters));
	}

	function selectSection(id: GenerationSection) {
		applyFilters({ ...filters, status: statusFromSection(id) });
	}

	function onPageChange(next: number) {
		pageIndex = next;
		void loadGenerations();
	}

	function onPageSizeChange(next: number) {
		pageSize = next;
		pageIndex = 1;
		void loadGenerations();
	}

	function openGenerationId(id: string) {
		const url = new URL($page.url);
		url.searchParams.set('id', id);
		void goto(url);
	}

	function backToList() {
		const url = new URL($page.url);
		url.searchParams.delete('id');
		void goto(url);
	}

	async function handleBulkDelete() {
		const ids = [...selected];
		if (ids.length === 0) return;
		const confirmed = await confirmDialog({
			title: `Delete ${ids.length} generation${ids.length === 1 ? '' : 's'}?`,
			message:
				'Their files are removed from disk. This cannot be undone.',
			variant: 'danger'
		});
		if (!confirmed) return;
		bulkDeleting = true;
		try {
			const response = await adminApi.adminBulkDeleteGenerations(ids);
			if (response.success && response.data) {
				const { deleted_count, failed_count } = response.data;
				if (failed_count > 0) {
					toasts.error(`Deleted ${deleted_count}, failed to delete ${failed_count}`);
				} else {
					toasts.success(`Deleted ${deleted_count} generation${deleted_count === 1 ? '' : 's'}`);
				}
			} else {
				toasts.error(response.message || 'Failed to delete generations');
			}
		} catch (e: any) {
			logger.error('Failed to bulk delete generations:', e);
			toasts.error(e?.response?.data?.message || e?.message || 'Failed to delete generations');
		} finally {
			bulkDeleting = false;
			selected = new Set();
			void loadGenerations();
		}
	}
</script>

{#snippet statusCell(row: AdminGenerationListItem)}
	<StatusCell tone={STATUS_TONE[row.status] ?? 'muted'} label={row.status} />
{/snippet}

{#snippet createdCell(row: AdminGenerationListItem)}
	<Tooltip text={absoluteTime(row.created_at)}><span>{timeAgo(row.created_at)}</span></Tooltip>
{/snippet}

{#snippet ratingCell(row: AdminGenerationListItem)}
	<span class="inline-flex items-center gap-1 justify-end w-full">
		{#if row.is_favorite}<Icon name="star" className="w-3 h-3 text-warning" strokeWidth={2.5} />{/if}
		<span>{row.rating > 0 ? row.rating : '—'}</span>
	</span>
{/snippet}

{#snippet cardBody(row: AdminGenerationListItem)}
	<div class="flex items-center justify-between gap-2 mb-1">
		<span class="truncate text-sm font-semibold text-fg">{presetTitleFor(row)}</span>
		<StatusCell tone={STATUS_TONE[row.status] ?? 'muted'} label={row.status} />
	</div>
	<div class="font-mono text-xs text-fg-subtle">{usernameFor(row.user_id)} · {timeAgo(row.created_at)} · {durationFor(row)}</div>
{/snippet}

<LibraryShell
	title="Generations"
	persistKey="admin-generations-library"
	heightClass="h-full"
	sections={GENERATION_LIBRARY_SECTIONS}
	{section}
	onSelectSection={selectSection}
	sectionCounts={sectionCountsCache}
	count={total}
	{detailOpen}
	filterChips={chips}
	{onRemoveChip}
	onClearFilters={onClearAllFilters}
	loadedCount={generations.length}
	{total}
>
	{#snippet toolbar()}
		<LibraryFilterBar
			q={filters.q}
			{onQueryChange}
			searchPlaceholder="Search by user, preset, id…"
			sortBy={filters.sortBy}
			sortOptions={GENERATION_SORT_OPTIONS}
			{onSortChange}
			filterCount={activeFilterCount}
		>
			{#snippet popover(close: () => void)}
				<GenerationsFiltersPopover {filters} {users} onChange={onPopoverChange} onClose={close} />
			{/snippet}
		</LibraryFilterBar>
	{/snippet}

	{#if detailOpen}
		{#if detailLoading && !detail}
			<div class="flex h-full items-center justify-center">
				<Spinner size="lg" />
			</div>
		{:else if detailError && !detail}
			<div class="flex h-full items-center justify-center">
				<EmptyState title="Could not load generation" description={detailError} icon="warning" compact>
					{#snippet actions()}<Button variant="ghost" size="sm" onclick={backToList}>Back to generations</Button>{/snippet}
				</EmptyState>
			</div>
		{:else if detail}
			<GenerationRunReport
				generation={detail.generation}
				report={detail.run_report}
				username={usernameFor(detail.generation.user_id)}
				backLabel="Generations"
				onBack={backToList}
			/>
		{/if}
	{:else}
		<div class="flex flex-col p-4 gap-3">
			<DataTable
				columns={[
					{ key: 'status', label: 'Status', width: '110px', cell: statusCell },
					{ key: 'preset', label: 'Preset', width: 'minmax(160px,1.4fr)', accessor: presetTitleFor },
					{ key: 'mode', label: 'Mode', width: '90px', priority: 1, mono: true, accessor: (r) => r.mode || '—' },
					{ key: 'user', label: 'User', width: '140px', priority: 1, accessor: (r) => usernameFor(r.user_id) },
					{ key: 'created', label: 'Created', width: '130px', sortable: true, mono: true, cell: createdCell },
					{ key: 'duration', label: 'Duration', width: '90px', priority: 1, mono: true, accessor: durationFor },
					{
						key: 'files',
						label: 'Files',
						width: '70px',
						priority: 1,
						align: 'right',
						mono: true,
						accessor: (r) => r.files?.length ?? 0
					},
					{ key: 'rating', label: 'Rating', width: '80px', priority: 1, align: 'right', cell: ratingCell }
				]}
				rows={generations}
				getRowId={(row) => row.id}
				{sort}
				onSortChange={onTableSortChange}
				onRowClick={(row) => openGenerationId(row.id)}
				selected={selected}
				onSelectedChange={(next) => (selected = next)}
				loading={listLoading}
				isFiltered={activeFilterCount > 0}
				card={cardBody}
			>
				{#snippet emptyState()}
					{#if listError}
						<EmptyState title="Could not load generations" description={listError} icon="warning" compact />
					{:else}
						<EmptyState
							icon="generation"
							title="No generations yet"
							description="Generations show up here once a user runs one."
							compact
						/>
					{/if}
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState
						icon="search"
						title="No generations match your filters"
						description="Try a different search, status, or date range."
						compact
					>
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={onClearAllFilters}>Clear filters</Button>{/snippet}
					</EmptyState>
				{/snippet}
			</DataTable>

			<TablePager
				page={pageIndex}
				pageCount={pageCount(total, pageSize)}
				{pageSize}
				{onPageChange}
				{onPageSizeChange}
			/>
		</div>
	{/if}
</LibraryShell>

<SelectionActionBar
	active={selected.size > 0}
	selectedCount={selected.size}
	totalCount={generations.length}
	onSelectAll={() => (selected = selectPage(selected, generations.map((g) => g.id)))}
	onClearSelection={() => (selected = clearAll())}
	onClose={() => (selected = clearAll())}
>
	<svelte:fragment slot="actionsBeforeCollection">
		<button
			class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium disabled:opacity-50"
			disabled={bulkDeleting}
			onclick={handleBulkDelete}
		>
			<Icon name="trash" className="w-4 h-4" />
			{bulkDeleting ? 'Deleting…' : 'Delete'}
		</button>
	</svelte:fragment>
</SelectionActionBar>
