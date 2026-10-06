<script lang="ts" generics="Row">
	import { untrack, type Snippet } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
		import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryFilterChipRow from '$lib/components/library/LibraryFilterChipRow.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SelectFilterField from '$lib/components/library/SelectFilterField.svelte';
	import { Button, EmptyState, Kbd, LoadErrorState, SegmentedControl } from '$lib/components/ui';
	import EntityRows from './EntityRows.svelte';
	import { buildCollisionIndex } from './pickFacts';
	import {
		activeFilterChips,
		applyLabel,
		clearFiltered,
		computeDiff,
		diffIsEmpty,
		diffSummary,
		filterRows,
		filteredSelectionState,
		rowsForView,
		selectFiltered,
		viewCounts
	} from './pickerState';
	import { failureSummary } from './applyDiff';
	import type {
		ApplyResult,
		EntityKind,
		FilterOption,
		PickerColumn,
		PickerDiff,
		PickerView,
		RemoteSource
	} from './types';

	let {
		kind,
		items = [],
		assignedIds = new Set<string>(),
		isOpen,
		title,
		subtitle = '',
		mode = 'multi',
		allowUnassign = true,
		confirmLabel,
		loading = false,
		error = null,
		onRetry,
		onApply,
		onClose,
		remote,
		extraColumns = [],
		emptyAction,
		forceNarrow
	}: {
		kind: EntityKind<Row>;
		items?: readonly Row[];
		assignedIds?: ReadonlySet<string>;
		isOpen: boolean;
		title: string;
		subtitle?: string;
		mode?: 'multi' | 'single';
		allowUnassign?: boolean;
		confirmLabel?: string;
		loading?: boolean;
		error?: string | null;
		onRetry?: () => void;
		onApply: (diff: PickerDiff) => Promise<ApplyResult | void> | ApplyResult | void;
		onClose: () => void;
		remote?: RemoteSource<Row>;
		extraColumns?: readonly PickerColumn<Row>[];
		emptyAction?: Snippet;
		forceNarrow?: boolean;
	} = $props();

	const multi = $derived(mode === 'multi');

	let baseline = $state<Set<string>>(new Set());
	let selected = $state<Set<string>>(new Set());
	let q = $state('');
	let filters = $state<Record<string, string>>({});
	let sort = $state('');
	let view = $state<PickerView>('unassigned');
	let busy = $state(false);
	let rowErrors = $state<Record<string, string>>({});
	let failureNote = $state('');
	let applyError = $state('');
	let searchInputEl = $state<HTMLInputElement | undefined>();
	let bodyEl = $state<HTMLDivElement | undefined>();

	let remoteRows = $state<Row[]>([]);
	let remoteTotal = $state(0);
	let remoteLoading = $state(false);
	let remoteError = $state<string | null>(null);
	let remoteToken = 0;
	let remoteTimer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		if (!isOpen) return;
		untrack(() => {
			baseline = new Set(assignedIds);
			selected = new Set(assignedIds);
			q = '';
			filters = {};
			sort = kind.defaultSort;
			view = 'unassigned';
			rowErrors = {};
			failureNote = '';
			applyError = '';
		});
	});

	$effect(() => {
		const next = new Set(assignedIds);
		untrack(() => {
			baseline = next;
		});
	});

	const sourceRows = $derived<readonly Row[]>(remote ? remoteRows : items);
	const collisions = $derived(buildCollisionIndex(sourceRows, kind));
	const sourceIds = $derived(sourceRows.map(kind.getId));
	const activeView = $derived<PickerView>(multi ? view : 'all');

	const filtered = $derived.by(() => {
		if (remote) return remoteRows;
		const byView = rowsForView(items, kind.getId, activeView, baseline);
		return filterRows(byView, kind, { q, filters, sort }, collisions);
	});
	const filteredIds = $derived(filtered.map(kind.getId));

	const lockedIds = $derived<ReadonlySet<string>>(
		multi && (activeView === 'all' || !allowUnassign) ? new Set(baseline) : new Set<string>()
	);

	const filterOptions = $derived.by(() => {
		const out: Record<string, FilterOption[]> = {};
		for (const filter of kind.filters) {
			out[filter.key] = remote?.filterOptions?.[filter.key] ?? filter.options(sourceRows);
		}
		return out;
	});
	const visibleFilters = $derived(kind.filters.filter((filter) => (filterOptions[filter.key]?.length ?? 0) > 1));
	const chips = $derived(activeFilterChips(kind, filters, filterOptions));

	const diff = $derived(computeDiff(baseline, selected, sourceIds));
	const effectiveDiff = $derived<PickerDiff>(allowUnassign ? diff : { add: diff.add, remove: [] });
	const headState = $derived(filteredSelectionState(filteredIds, selected, lockedIds));
	const counts = $derived(viewCounts(sourceIds, baseline));

	const viewItems = $derived(
		remote
			? [
					{ id: 'unassigned', label: 'Not assigned' },
					{ id: 'assigned', label: 'Assigned', count: baseline.size },
					{ id: 'all', label: 'All' }
				]
			: [
					{ id: 'unassigned', label: 'Not assigned', count: counts.unassigned },
					{ id: 'assigned', label: 'Assigned', count: counts.assigned },
					{ id: 'all', label: 'All', count: counts.all }
				]
	);

	const isFiltered = $derived(q.trim() !== '' || chips.length > 0 || (multi && activeView !== 'all' && sourceRows.length > 0));
	const canApply = $derived(mode === 'single' ? selected.size === 1 && !diffIsEmpty(effectiveDiff) : !diffIsEmpty(effectiveDiff));
	const footerSummary = $derived(
		multi
			? `${selected.size} selected${diffIsEmpty(effectiveDiff) ? '' : ` · ${diffSummary(effectiveDiff)}`}`
			: selected.size > 0
				? '1 selected'
				: ''
	);
	const primaryLabel = $derived(
		confirmLabel ?? (multi ? applyLabel(effectiveDiff, kind.plural, kind.singular) : 'Select')
	);

	function setSelected(next: Set<string>) {
		selected = next;
	}

	function toggleHeader() {
		selected = headState === 'all' ? clearFiltered(selected, filteredIds, lockedIds) : selectFiltered(selected, filteredIds, lockedIds);
	}

	function selectAllFiltered() {
		selected = selectFiltered(selected, filteredIds, lockedIds);
	}

	function clearSelection() {
		selected = new Set(baseline);
	}

	function clearAllFilters() {
		q = '';
		filters = {};
		if (multi) view = 'all';
	}

	async function apply() {
		if (busy || !canApply) return;
		busy = true;
		rowErrors = {};
		failureNote = '';
		applyError = '';
		try {
			const result = await onApply(effectiveDiff);
			if (result && result.failed.length > 0) {
				rowErrors = Object.fromEntries(result.failed.map((failure) => [failure.id, failure.message]));
				failureNote = failureSummary(result, kind.plural);
				const next = new Set(baseline);
				for (const id of result.ok) {
					if (effectiveDiff.add.includes(id)) next.add(id);
					else next.delete(id);
				}
				baseline = next;
				return;
			}
			onClose();
		} catch (caught) {
			applyError = caught instanceof Error && caught.message ? caught.message : 'The change could not be applied';
		} finally {
			busy = false;
		}
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (!isOpen) return;
		if (event.key === 'Escape' && !event.repeat) {
			if (document.querySelector('[role="dialog"][aria-label="Filters"]')) return;
			event.preventDefault();
			event.stopPropagation();
			if (q !== '') q = '';
			else onClose();
			return;
		}
		if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.repeat) {
			event.preventDefault();
			void apply();
		}
	}

	function handleSearchKeydown(event: KeyboardEvent) {
		if (event.key !== 'ArrowDown') return;
		const first = bodyEl?.querySelector<HTMLElement>('[data-row-index="0"]');
		if (first) {
			event.preventDefault();
			first.focus();
		}
	}

	$effect(() => {
		const input = searchInputEl;
		if (!input) return;
		input.addEventListener('keydown', handleSearchKeydown);
		return () => input.removeEventListener('keydown', handleSearchKeydown);
	});

	function loadRemote(reset: boolean) {
		if (!remote) return;
		const token = ++remoteToken;
		const limit = remote.pageSize ?? 60;
		remoteLoading = true;
		remoteError = null;
		const offset = reset ? 0 : remoteRows.length;
		remote
			.fetch({ q, filters: { ...filters }, sort, view: activeView, offset, limit })
			.then((page) => {
				if (token !== remoteToken) return;
				remoteRows = reset ? page.rows : [...remoteRows, ...page.rows];
				remoteTotal = page.total;
			})
			.catch((caught) => {
				if (token !== remoteToken) return;
				remoteError = caught instanceof Error && caught.message ? caught.message : 'Could not load the list';
			})
			.finally(() => {
				if (token === remoteToken) remoteLoading = false;
			});
	}

	function loadMore() {
		if (remote && !remoteLoading && remoteRows.length < remoteTotal) loadRemote(false);
	}

	$effect(() => {
		if (!isOpen || !remote) return;
		void q;
		void filters;
		void sort;
		void activeView;
		clearTimeout(remoteTimer);
		remoteTimer = setTimeout(() => untrack(() => loadRemote(true)), 250);
		return () => clearTimeout(remoteTimer);
	});

	const shownLoading = $derived(remote ? remoteLoading && remoteRows.length === 0 : loading);
	const shownError = $derived(error ?? (remote ? remoteError : null));
	const searchHint = $derived(remote ? `${remoteRows.length} of ${remoteTotal}` : `${filtered.length}`);
	const emptyView = $derived(
		activeView === 'unassigned' && q.trim() === '' && chips.length === 0 && baseline.size > 0 && sourceRows.length > 0
			? 'Everything is already assigned.'
			: ''
	);
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<BaseModal
	{isOpen}
	{title}
	{subtitle}
	size="xl"
	sizeClass="md:max-w-5xl md:w-full"
	handleEscapeKey={false}
	on:close={onClose}
>
	<div class="flex flex-col gap-3 p-4" bind:this={bodyEl}>
		<div class="flex flex-wrap items-center gap-2">
			<LibraryFilterBar
				{q}
				onQueryChange={(value) => (q = value)}
				searchPlaceholder={kind.searchPlaceholder}
				searchAriaLabel={`Search ${kind.plural}`}
				{searchHint}
				bind:searchInputEl
				sortBy={sort}
				sortOptions={kind.sorts.map((s) => ({ value: s.value, label: s.label }))}
				onSortChange={(value) => (sort = value)}
				filterCount={chips.length}
			>
				{#snippet popover(close)}
					{#if visibleFilters.length > 0}
						<FilterPopoverFrame
							width="w-[24rem]"
							onClearAll={() => (filters = {})}
							onClose={close}
						>
							{#each visibleFilters as filter (filter.key)}
								<SelectFilterField
									label={filter.label}
									value={filters[filter.key] ?? ''}
									options={filterOptions[filter.key]}
									onChange={(value) => (filters = { ...filters, [filter.key]: value })}
								/>
							{/each}
						</FilterPopoverFrame>
					{/if}
				{/snippet}
			</LibraryFilterBar>
		</div>

		{#if multi}
			<div>
				<SegmentedControl
					items={viewItems}
					selected={view}
					onSelect={(id) => (view = id as PickerView)}
					ariaLabel={`${kind.plural} view`}
				/>
			</div>
		{/if}

		{#if chips.length > 0}
			<LibraryFilterChipRow
				{chips}
				onRemoveChip={(key) => (filters = { ...filters, [key]: '' })}
				onClearAll={() => (filters = {})}
				loadedCount={filtered.length}
				total={sourceRows.length}
			/>
		{/if}

		{#if shownError}
			<LoadErrorState message={shownError} onRetry={() => (remote && !error ? loadRemote(true) : onRetry?.())} />
		{:else}
			<EntityRows
				{kind}
				rows={filtered}
				{collisions}
				{selected}
				onSelectedChange={setSelected}
				selectionType={multi ? 'multi' : 'single'}
				{lockedIds}
				assignedIds={baseline}
				{rowErrors}
				{extraColumns}
				loading={shownLoading}
				{isFiltered}
				virtual
				scrollMaxHeight="min(calc(100dvh - 22rem), 34rem)"
				onNearEnd={remote ? loadMore : undefined}
				headerState={headState}
				onHeaderToggle={toggleHeader}
				headerLabel={`Select all ${filtered.length} filtered ${kind.plural}`}
				onSelectAll={selectAllFiltered}
				onConfirm={() => void apply()}
				ariaLabel={`${kind.plural} to pick from`}
				{forceNarrow}
			>
				{#snippet emptyState()}
					<EmptyState icon={kind.icon} title={kind.empty.title} description={kind.empty.description} compact>
						{#snippet actions()}{@render emptyAction?.()}{/snippet}
					</EmptyState>
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState
						icon="search"
						title={emptyView || `No ${kind.plural} match`}
						description={emptyView ? 'Switch the view to All to see them.' : 'Try a different search or clear the filters.'}
						compact
					>
						{#snippet actions()}
							<Button size="sm" variant="secondary" onclick={clearAllFilters}>Clear filters</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
			</EntityRows>
			{#if remote && remoteRows.length < remoteTotal}
				<div class="flex justify-center">
					<Button size="sm" variant="ghost" loading={remoteLoading} onclick={loadMore}>
						Load more ({remoteTotal - remoteRows.length} left)
					</Button>
				</div>
			{/if}
		{/if}

		{#if failureNote || applyError}
			<div class="rounded border border-danger/25 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">
				{applyError || failureNote}
			</div>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-between gap-3 px-4 py-3 sm:px-6 sm:py-4">
			<div class="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
				<span class="font-mono text-xs tabular-nums text-fg-muted" data-picker-summary>{footerSummary}</span>
				{#if multi && filtered.length > 0 && headState !== 'all'}
					<button type="button" class="text-xs text-signal hover:underline" onclick={selectAllFiltered}>
						Select all {filtered.length} filtered
					</button>
				{/if}
				{#if multi && !diffIsEmpty(effectiveDiff)}
					<button type="button" class="text-xs text-fg-subtle hover:text-fg" onclick={clearSelection}>Reset</button>
				{/if}
			</div>
			<div class="ml-auto flex flex-shrink-0 items-center gap-3">
				<Button variant="secondary" disabled={busy} onclick={onClose}>
					<span class="inline-flex items-center gap-2">Cancel <Kbd keys="Esc" /></span>
				</Button>
				<Button variant="primary" disabled={!canApply} loading={busy} onclick={() => void apply()}>
					<span class="inline-flex items-center gap-2">{primaryLabel} <Kbd keys={['Ctrl', 'Enter']} /></span>
				</Button>
			</div>
		</div>
	</svelte:fragment>
</BaseModal>
