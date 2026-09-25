<script lang="ts" generics="Row">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { alignClass, gridTemplateColumns, visibleColumns, type DataTableColumn } from './column';
	import { cycleSort, type SortState } from './sortState';
	import { clearPage, pageSelectionState, selectPage, toggleRow } from './selection';

	let {
		columns,
		rows,
		getRowId,
		sort = null,
		onSortChange,
		onRowClick,
		selected,
		onSelectedChange,
		detail = false,
		loading = false,
		loadingRowCount = 5,
		emptyState,
		filteredEmptyState,
		isFiltered = false,
		rowActions,
		card,
		class: className = ''
	}: {
		columns: readonly DataTableColumn<Row>[];
		rows: readonly Row[];
		getRowId: (row: Row) => string;
		sort?: SortState | null;
		onSortChange?: (sort: SortState | null) => void;
		onRowClick?: (row: Row) => void;
		selected?: ReadonlySet<string>;
		onSelectedChange?: (next: Set<string>) => void;
		detail?: boolean;
		loading?: boolean;
		loadingRowCount?: number;
		emptyState?: Snippet;
		filteredEmptyState?: Snippet;
		isFiltered?: boolean;
		rowActions?: Snippet<[Row]>;
		card?: Snippet<[Row]>;
		class?: string;
	} = $props();

	const selectable = $derived(!!selected && !!onSelectedChange);
	const shownColumns = $derived(visibleColumns(columns, { detail }));
	const colsFull = $derived(gridTemplateColumns(shownColumns, { narrow: false, selectable, trailing: !!rowActions }));
	const colsNarrow = $derived(gridTemplateColumns(shownColumns, { narrow: true, selectable, trailing: !!rowActions }));
	const rowIds = $derived(rows.map(getRowId));
	const headState = $derived(selected ? pageSelectionState(rowIds, selected) : 'none');

	function handleSort(column: DataTableColumn<Row>) {
		if (!column.sortable || !onSortChange) return;
		onSortChange(cycleSort(sort, column.key));
	}

	function handleHeaderCheckbox() {
		if (!selected || !onSelectedChange) return;
		onSelectedChange(headState === 'all' ? clearPage(selected, rowIds) : selectPage(selected, rowIds));
	}

	function handleRowCheckbox(id: string) {
		if (!selected || !onSelectedChange) return;
		onSelectedChange(toggleRow(selected, id));
	}

	function handleRowActivate(row: Row) {
		onRowClick?.(row);
	}

	function handleRowKeydown(event: KeyboardEvent, row: Row) {
		if (event.key !== 'Enter') return;
		event.preventDefault();
		handleRowActivate(row);
	}
</script>

<div
	class="dt-root rounded-lg border border-line-strong bg-surface-1 {className}"
	style="--dt-cols-full: {colsFull}; --dt-cols-narrow: {colsNarrow};"
>
	<div class="dt-scroll">
		<div class="dt-row dt-row--head sticky top-0 z-[1] min-h-8 items-center gap-2.5 rounded-t-lg border-b border-line bg-surface-2 px-2.5 font-mono text-xs uppercase tracking-[0.06em] text-fg-subtle">
			{#if selectable}
				<span class="flex items-center justify-center">
					<button
						type="button"
						role="checkbox"
						aria-checked={headState === 'all' ? 'true' : headState === 'some' ? 'mixed' : 'false'}
						aria-label="Select all rows on this page"
						class="flex h-4 w-4 items-center justify-center rounded border border-line-strong bg-surface-2 {headState !== 'none' ? 'border-signal bg-signal' : ''}"
						onclick={handleHeaderCheckbox}
					>
						{#if headState === 'all'}
							<Icon name="check" className="h-2.5 w-2.5 text-canvas" strokeWidth={3} />
						{:else if headState === 'some'}
							<span class="h-0.5 w-2 rounded-full bg-canvas" aria-hidden="true"></span>
						{/if}
					</button>
				</span>
			{/if}
			{#each shownColumns as column (column.key)}
				<span class="dt-col {(column.priority ?? 0) === 1 ? 'dt-col--p1' : ''} flex items-center {alignClass(column.align)}">
					{#if column.sortable}
						<button
							type="button"
							class="inline-flex items-center gap-1 {sort?.key === column.key ? 'text-signal' : ''}"
							onclick={() => handleSort(column)}
						>
							{column.label}
							<Icon
								name={sort?.key === column.key && sort.dir === 'desc' ? 'chevron-down' : 'chevron-up'}
								className="h-2.5 w-2.5 {sort?.key === column.key ? 'opacity-100' : 'opacity-0'}"
							/>
						</button>
					{:else}
						{column.label}
					{/if}
				</span>
			{/each}
			{#if rowActions}<span class="dt-col"></span>{/if}
		</div>

		{#if loading}
			{#each Array(loadingRowCount) as _, i (i)}
				<div class="dt-row min-h-[42px] items-center gap-2.5 border-b border-line px-2.5">
					{#if selectable}<span></span>{/if}
					{#each shownColumns as column, ci (column.key)}
						<span class="dt-col {(column.priority ?? 0) === 1 ? 'dt-col--p1' : ''} flex items-center">
							<span
								class="h-2.5 animate-pulse rounded bg-surface-3"
								style="width: {ci === 0 ? '70%' : '50px'};"
							></span>
						</span>
					{/each}
					{#if rowActions}<span class="dt-col"></span>{/if}
				</div>
			{/each}
		{:else if rows.length === 0}
			<div class="flex flex-col items-center gap-2.5 px-5 py-11 text-center">
				{#if isFiltered && filteredEmptyState}
					{@render filteredEmptyState()}
				{:else if emptyState}
					{@render emptyState()}
				{/if}
			</div>
		{:else}
			{#each rows as row (getRowId(row))}
				{@const id = getRowId(row)}
				{@const rowSelected = selected?.has(id) ?? false}
				<div
					class="dt-row {rowSelected ? 'dt-row--selected bg-signal/[0.07] shadow-[inset_2px_0_0_0_rgb(var(--signal))]' : ''} min-h-[42px] items-center gap-2.5 border-b border-line px-2.5 {onRowClick ? 'cursor-pointer hover:bg-surface-2/50' : ''}"
					role="row"
					tabindex={onRowClick ? 0 : -1}
					onclick={(event) => {
						if ((event.target as HTMLElement).closest('[data-row-actions]')) return;
						handleRowActivate(row);
					}}
					onkeydown={(event) => handleRowKeydown(event, row)}
				>
					{#if selectable}
						<span class="flex items-center justify-center">
							<button
								type="button"
								role="checkbox"
								aria-checked={rowSelected}
								aria-label="Select row"
								class="flex h-4 w-4 items-center justify-center rounded border border-line-strong bg-surface-2 {rowSelected ? 'border-signal bg-signal' : ''}"
								onclick={(event) => {
									event.stopPropagation();
									handleRowCheckbox(id);
								}}
							>
								{#if rowSelected}
									<Icon name="check" className="h-2.5 w-2.5 text-canvas" strokeWidth={3} />
								{/if}
							</button>
						</span>
					{/if}
					{#each shownColumns as column (column.key)}
						<span
							class="dt-col {(column.priority ?? 0) === 1 ? 'dt-col--p1' : ''} flex items-center overflow-hidden text-ellipsis whitespace-nowrap text-xs {column.mono ? 'font-mono tabular-nums' : ''} {alignClass(column.align)}"
						>
							{#if column.cell}
								{@render column.cell(row)}
							{:else}
								{column.accessor?.(row) ?? '—'}
							{/if}
						</span>
					{/each}
					{#if rowActions}
						<span class="dt-col dt-row-actions flex items-center justify-end" data-row-actions>
							{@render rowActions(row)}
						</span>
					{/if}
				</div>
			{/each}
		{/if}

		<div class="dt-mobile hidden">
			{#if !loading}
				{#each rows as row (getRowId(row))}
					<div
						class="flex items-start gap-2.5 border-b border-line px-3.5 py-3 {onRowClick ? 'cursor-pointer' : ''}"
						role="row"
						tabindex={onRowClick ? 0 : -1}
						onclick={() => handleRowActivate(row)}
						onkeydown={(event) => handleRowKeydown(event, row)}
					>
						<div class="min-w-0 flex-1">
							{#if card}
								{@render card(row)}
							{:else if shownColumns[0]}
								<div class="truncate text-sm font-semibold text-fg">
									{#if shownColumns[0].cell}
										{@render shownColumns[0].cell(row)}
									{:else}
										{shownColumns[0].accessor?.(row) ?? '—'}
									{/if}
								</div>
							{/if}
						</div>
						{#if onRowClick}
							<Icon name="chevron-right" className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
						{/if}
					</div>
				{/each}
			{/if}
		</div>
	</div>
</div>

<style>
	.dt-root {
		container-type: inline-size;
		container-name: data-table;
	}

	.dt-row {
		display: grid;
		grid-template-columns: var(--dt-cols-full);
	}

	@container data-table (max-width: 56.25rem) {
		.dt-row {
			grid-template-columns: var(--dt-cols-narrow);
		}

		.dt-col--p1 {
			display: none;
		}
	}

	@container data-table (max-width: 40rem) {
		.dt-scroll > .dt-row {
			display: none;
		}

		.dt-mobile {
			display: block !important;
		}
	}

	.dt-row-actions {
		opacity: 0;
		transition: opacity 100ms;
	}

	.dt-row:hover .dt-row-actions,
	.dt-row:focus-within .dt-row-actions {
		opacity: 1;
	}
</style>
