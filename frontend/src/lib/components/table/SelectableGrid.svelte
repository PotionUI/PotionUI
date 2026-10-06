<script lang="ts" generics="Row">
	import { tick, type Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { alignClass, gridTemplateColumns, visibleColumns, type DataTableColumn } from './column';
	import { cycleSort, type SortState } from './sortState';
	import {
		applyRange,
		moveActiveIndex,
		pageSelectionState,
		rangeBetween,
		toggleRow,
		type PageSelectionState
	} from './selection';
	import { scrollTopToReveal, shouldVirtualize, virtualWindow } from './virtualWindow';

	const NARROW_MAX = 640;
	const HEADER_HEIGHT = 32;

	let {
		columns,
		rows,
		getRowId,
		sort = null,
		onSortChange,
		selected,
		onSelectedChange,
		selectionType = 'multi',
		isLocked,
		rowClass,
		headerState,
		onHeaderToggle,
		headerLabel = 'Select all rows',
		onSelectAll,
		onConfirm,
		detail = false,
		loading = false,
		loadingRowCount = 5,
		emptyState,
		filteredEmptyState,
		isFiltered = false,
		rowActions,
		card,
		virtual = false,
		rowHeight = 58,
		cardHeight = 96,
		scrollMaxHeight,
		onNearEnd,
		ariaLabel = 'Rows',
		forceNarrow,
		class: className = ''
	}: {
		columns: readonly DataTableColumn<Row>[];
		rows: readonly Row[];
		getRowId: (row: Row) => string;
		sort?: SortState | null;
		onSortChange?: (sort: SortState | null) => void;
		selected: ReadonlySet<string>;
		onSelectedChange: (next: Set<string>) => void;
		selectionType?: 'multi' | 'single';
		isLocked?: (row: Row) => boolean;
		rowClass?: (row: Row) => string;
		headerState?: PageSelectionState;
		onHeaderToggle?: () => void;
		headerLabel?: string;
		onSelectAll?: () => void;
		onConfirm?: () => void;
		detail?: boolean;
		loading?: boolean;
		loadingRowCount?: number;
		emptyState?: Snippet;
		filteredEmptyState?: Snippet;
		isFiltered?: boolean;
		rowActions?: Snippet<[Row]>;
		card?: Snippet<[Row]>;
		virtual?: boolean;
		rowHeight?: number;
		cardHeight?: number;
		scrollMaxHeight?: string;
		onNearEnd?: () => void;
		ariaLabel?: string;
		forceNarrow?: boolean;
		class?: string;
	} = $props();

	const multi = $derived(selectionType === 'multi');
	const shownColumns = $derived(visibleColumns(columns, { detail }));
	const colsFull = $derived(gridTemplateColumns(shownColumns, { narrow: false, selectable: true, trailing: !!rowActions }));
	const colsNarrow = $derived(gridTemplateColumns(shownColumns, { narrow: true, selectable: true, trailing: !!rowActions }));
	const rowIds = $derived(rows.map(getRowId));
	const lockedIds = $derived(new Set(isLocked ? rows.filter((row) => isLocked(row)).map(getRowId) : []));
	const headState = $derived(headerState ?? pageSelectionState(rowIds, selected));

	let rootWidth = $state(0);
	const narrow = $derived(forceNarrow ?? (rootWidth > 0 && rootWidth <= NARROW_MAX));
	const itemHeight = $derived(narrow ? cardHeight : rowHeight);
	const windowed = $derived(shouldVirtualize(rows.length, virtual));

	let scroller: HTMLDivElement | undefined = $state();
	let scrollTop = $state(0);
	let viewport = $state(0);
	let activeIndex = $state(-1);
	let anchor = $state<{ id: string; selects: boolean } | null>(null);
	let extend: { base: Set<string>; anchorId: string } | null = null;

	const win = $derived(
		windowed
			? virtualWindow({ count: rows.length, rowHeight: itemHeight, scrollTop, viewport: viewport || 640 })
			: { start: 0, end: rows.length, padTop: 0, padBottom: 0 }
	);
	const visibleRows = $derived(rows.slice(win.start, win.end));

	$effect(() => {
		if (activeIndex >= rows.length) activeIndex = rows.length - 1;
	});

	function handleScroll() {
		if (!scroller) return;
		scrollTop = scroller.scrollTop;
		viewport = scroller.clientHeight;
		if (onNearEnd && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < itemHeight * 6) onNearEnd();
	}

	function handleSort(column: DataTableColumn<Row>) {
		if (!column.sortable || !onSortChange) return;
		onSortChange(cycleSort(sort, column.key));
	}

	function rowToggle(id: string, shift: boolean, index: number) {
		extend = null;
		if (lockedIds.has(id)) return;
		activeIndex = index;
		if (!multi) {
			onSelectedChange(new Set([id]));
			return;
		}
		if (shift && anchor && rowIds.includes(anchor.id)) {
			onSelectedChange(applyRange(selected, rangeBetween(rowIds, anchor.id, id), anchor.selects, lockedIds));
			return;
		}
		const next = toggleRow(selected, id);
		anchor = { id, selects: next.has(id) };
		onSelectedChange(next);
	}

	function handleRowClick(event: MouseEvent, row: Row, index: number) {
		const target = event.target as HTMLElement;
		if (target.closest('a[href], button, input, select, textarea, [data-row-actions], [data-no-toggle]')) return;
		rowToggle(getRowId(row), event.shiftKey, index);
	}

	async function focusRow(index: number) {
		if (index < 0 || !scroller) return;
		if (windowed) {
			const next = scrollTopToReveal({
				index,
				rowHeight: itemHeight,
				scrollTop: scroller.scrollTop,
				viewport: scroller.clientHeight || 640,
				headerHeight: narrow ? 0 : HEADER_HEIGHT
			});
			scroller.scrollTop = next;
			scrollTop = next;
		}
		await tick();
		const el = scroller.querySelector<HTMLElement>(`[data-row-index="${index}"]`);
		el?.focus({ preventScroll: windowed });
		if (!windowed) el?.scrollIntoView?.({ block: 'nearest' });
	}

	function handleKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement;
		if (target.closest('input, select, textarea, [data-row-actions]')) return;
		const mod = event.ctrlKey || event.metaKey;
		if (mod && event.key.toLowerCase() === 'a' && multi) {
			event.preventDefault();
			onSelectAll?.();
			return;
		}
		if (mod && event.key === 'Enter') {
			event.preventDefault();
			onConfirm?.();
			return;
		}
		if (['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
			event.preventDefault();
			const next = moveActiveIndex(activeIndex, event.key, rows.length);
			if (next < 0) return;
			if (event.shiftKey && multi) {
				const anchorId = extend?.anchorId ?? (activeIndex >= 0 ? rowIds[activeIndex] : rowIds[next]);
				extend = extend ?? { base: new Set(selected), anchorId };
				onSelectedChange(applyRange(extend.base, rangeBetween(rowIds, extend.anchorId, rowIds[next]), true, lockedIds));
			} else {
				extend = null;
			}
			activeIndex = next;
			void focusRow(next);
			return;
		}
		if (event.key === ' ' && activeIndex >= 0 && !target.closest('button, a[href]')) {
			event.preventDefault();
			extend = null;
			rowToggle(rowIds[activeIndex], false, activeIndex);
		}
	}

	function handleFocusRow(index: number) {
		activeIndex = index;
	}

	const tabbableIndex = $derived(activeIndex >= 0 ? activeIndex : rows.length > 0 ? 0 : -1);
</script>

<div
	class="dt-root rounded-lg border border-line-strong bg-surface-1 {className}"
	style="--dt-cols-full: {colsFull}; --dt-cols-narrow: {colsNarrow};"
	bind:clientWidth={rootWidth}
>
	<div
		bind:this={scroller}
		class="dt-grid-scroll"
		style={scrollMaxHeight ? `max-height: ${scrollMaxHeight}; overflow-y: auto;` : ''}
		role="grid"
		aria-label={ariaLabel}
		aria-multiselectable={multi}
		aria-rowcount={rows.length + 1}
		tabindex="-1"
		onscroll={handleScroll}
	>
		{#if !narrow}
			<div
				class="dt-row dt-row--head sticky top-0 z-[1] items-center gap-2.5 rounded-t-lg border-b border-line bg-surface-2 px-2.5 font-mono text-xs uppercase tracking-[0.06em] text-fg-subtle"
				style="min-height: {HEADER_HEIGHT}px;"
				role="row"
				aria-rowindex={1}
			>
				<span class="flex items-center justify-center" role="columnheader" aria-label={multi ? headerLabel : 'Selected'}>
					{#if multi}
						<button
							type="button"
							role="checkbox"
							aria-checked={headState === 'all' ? 'true' : headState === 'some' ? 'mixed' : 'false'}
							aria-label={headerLabel}
							class="flex h-4 w-4 items-center justify-center rounded border border-line-strong bg-surface-2 {headState !== 'none' ? 'border-signal bg-signal' : ''}"
							onclick={() => onHeaderToggle?.()}
						>
							{#if headState === 'all'}
								<Icon name="check" className="h-2.5 w-2.5 text-canvas" strokeWidth={3} />
							{:else if headState === 'some'}
								<span class="h-0.5 w-2 rounded-full bg-canvas" aria-hidden="true"></span>
							{/if}
						</button>
					{/if}
				</span>
				{#each shownColumns as column (column.key)}
					<span
						class="dt-col {(column.priority ?? 0) === 1 ? 'dt-col--p1' : ''} flex items-center {alignClass(column.align)}"
						role="columnheader"
						aria-sort={sort?.key === column.key ? (sort.dir === 'desc' ? 'descending' : 'ascending') : undefined}
					>
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
				{#if rowActions}<span class="dt-col" role="columnheader" aria-label="Actions"></span>{/if}
			</div>
		{/if}

		{#if loading}
			{#each Array(loadingRowCount) as _, i (i)}
				<div class="dt-row items-center gap-2.5 border-b border-line px-2.5" style="height: {rowHeight}px;" role="presentation">
					<span></span>
					{#each shownColumns as column, ci (column.key)}
						<span class="dt-col {(column.priority ?? 0) === 1 ? 'dt-col--p1' : ''} flex items-center">
							<span class="h-2.5 animate-pulse rounded bg-surface-3" style="width: {ci === 0 ? '70%' : '50px'};"></span>
						</span>
					{/each}
					{#if rowActions}<span class="dt-col"></span>{/if}
				</div>
			{/each}
		{:else if rows.length === 0}
			<div class="flex flex-col items-center gap-2.5 px-5 py-11 text-center" role="row">
				<div role="gridcell">
					{#if isFiltered && filteredEmptyState}
						{@render filteredEmptyState()}
					{:else if emptyState}
						{@render emptyState()}
					{/if}
				</div>
			</div>
		{:else}
			{#if win.padTop > 0}<div style="height: {win.padTop}px;" role="presentation"></div>{/if}
			{#each visibleRows as row, vi (getRowId(row))}
				{@const index = win.start + vi}
				{@const id = getRowId(row)}
				{@const rowSelected = selected.has(id)}
				{@const locked = lockedIds.has(id)}
				{#if narrow}
					<div
						class="flex items-start gap-2.5 overflow-hidden border-b border-line px-3.5 py-3 outline-none focus-visible:shadow-[inset_0_0_0_2px_rgb(var(--signal))] {rowSelected ? 'bg-signal/[0.07]' : ''} {locked ? 'cursor-not-allowed' : 'cursor-pointer'} {rowClass?.(row) ?? ''}"
						style="height: {cardHeight}px;"
						role="row"
						aria-rowindex={index + 2}
						aria-selected={rowSelected}
						aria-disabled={locked ? 'true' : undefined}
						data-row-id={id}
						data-row-index={index}
						tabindex={index === tabbableIndex ? 0 : -1}
						onclick={(event) => handleRowClick(event, row, index)}
						onfocus={() => handleFocusRow(index)}
						onkeydown={handleKeydown}
					>
						<span class="mt-0.5 flex-shrink-0" role="gridcell">
							<span
								class="flex h-4 w-4 items-center justify-center border border-line-strong bg-surface-2 {multi ? 'rounded' : 'rounded-full'} {rowSelected ? 'border-signal bg-signal' : ''}"
								aria-hidden="true"
							>
								{#if rowSelected}<Icon name="check" className="h-2.5 w-2.5 text-canvas" strokeWidth={3} />{/if}
							</span>
						</span>
						<div class="min-w-0 flex-1" role="gridcell">
							{#if card}{@render card(row)}{:else if shownColumns[0]}{shownColumns[0].accessor?.(row) ?? '—'}{/if}
						</div>
						{#if rowActions}
							<div class="flex-shrink-0" role="gridcell" data-row-actions>{@render rowActions(row)}</div>
						{/if}
					</div>
				{:else}
					<div
						class="dt-row group items-center gap-2.5 overflow-hidden border-b border-line px-2.5 outline-none focus-visible:shadow-[inset_0_0_0_2px_rgb(var(--signal))] {rowSelected ? 'bg-signal/[0.07] shadow-[inset_2px_0_0_0_rgb(var(--signal))]' : 'hover:bg-surface-2/50'} {locked ? 'cursor-not-allowed' : 'cursor-pointer'} {rowClass?.(row) ?? ''}"
						style="height: {rowHeight}px;"
						role="row"
						aria-rowindex={index + 2}
						aria-selected={rowSelected}
						aria-disabled={locked ? 'true' : undefined}
						data-row-id={id}
						data-row-index={index}
						tabindex={index === tabbableIndex ? 0 : -1}
						onclick={(event) => handleRowClick(event, row, index)}
						onfocus={() => handleFocusRow(index)}
						onkeydown={handleKeydown}
					>
						<span class="flex items-center justify-center" role="gridcell">
							<span
								class="flex h-4 w-4 items-center justify-center border border-line-strong bg-surface-2 {multi ? 'rounded' : 'rounded-full'} {rowSelected ? 'border-signal bg-signal' : ''}"
								aria-hidden="true"
							>
								{#if rowSelected}<Icon name="check" className="h-2.5 w-2.5 text-canvas" strokeWidth={3} />{/if}
							</span>
						</span>
						{#each shownColumns as column (column.key)}
							<span
								class="dt-col {(column.priority ?? 0) === 1 ? 'dt-col--p1' : ''} flex min-w-0 items-center overflow-hidden text-ellipsis whitespace-nowrap text-xs {column.mono ? 'font-mono tabular-nums' : ''} {alignClass(column.align)}"
								role="gridcell"
							>
								{#if column.cell}
									{@render column.cell(row)}
								{:else}
									{column.accessor?.(row) ?? '—'}
								{/if}
							</span>
						{/each}
						{#if rowActions}
							<span class="dt-col dt-row-actions flex items-center justify-end" role="gridcell" data-row-actions>
								{@render rowActions(row)}
							</span>
						{/if}
					</div>
				{/if}
			{/each}
			{#if win.padBottom > 0}<div style="height: {win.padBottom}px;" role="presentation"></div>{/if}
		{/if}
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

	.dt-row-actions {
		opacity: 0;
		transition: opacity 100ms;
	}

	.dt-row:hover .dt-row-actions,
	.dt-row:focus-within .dt-row-actions,
	.dt-row[aria-selected='true'] .dt-row-actions {
		opacity: 1;
	}

	@media (hover: none) {
		.dt-row-actions {
			opacity: 1;
		}
	}
</style>
