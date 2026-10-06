<script lang="ts" generics="Row">
	import type { Snippet } from 'svelte';
	import { DataTable, type DataTableColumn, type PageSelectionState } from '$lib/components/table';
	import PickerPrimaryCell from './PickerPrimaryCell.svelte';
	import type { CollisionInfo, EntityKind, PickerColumn } from './types';

	let {
		kind,
		rows,
		collisions,
		selected,
		onSelectedChange,
		selectionType = 'multi',
		lockedIds,
		assignedIds,
		rowErrors,
		extraColumns = [],
		loading = false,
		isFiltered = false,
		emptyState,
		filteredEmptyState,
		rowActions,
		virtual = false,
		scrollMaxHeight,
		onNearEnd,
		headerState,
		onHeaderToggle,
		headerLabel,
		onSelectAll,
		onConfirm,
		ariaLabel,
		forceNarrow,
		class: className = ''
	}: {
		kind: EntityKind<Row>;
		rows: readonly Row[];
		collisions: ReadonlyMap<string, CollisionInfo>;
		selected: ReadonlySet<string>;
		onSelectedChange: (next: Set<string>) => void;
		selectionType?: 'multi' | 'single';
		lockedIds?: ReadonlySet<string>;
		assignedIds?: ReadonlySet<string>;
		rowErrors?: Readonly<Record<string, string>>;
		extraColumns?: readonly PickerColumn<Row>[];
		loading?: boolean;
		isFiltered?: boolean;
		emptyState?: Snippet;
		filteredEmptyState?: Snippet;
		rowActions?: Snippet<[Row]>;
		virtual?: boolean;
		scrollMaxHeight?: string;
		onNearEnd?: () => void;
		headerState?: PageSelectionState;
		onHeaderToggle?: () => void;
		headerLabel?: string;
		onSelectAll?: () => void;
		onConfirm?: () => void;
		ariaLabel?: string;
		forceNarrow?: boolean;
		class?: string;
	} = $props();

	const label = $derived(kind.singular.charAt(0).toUpperCase() + kind.singular.slice(1));
	const rowHeight = $derived(kind.rowHeight ?? 58);

	const columns = $derived<DataTableColumn<Row>[]>([
		{ key: 'primary', label, width: 'minmax(0, 1fr)', cell: primaryCell },
		...[...kind.columns, ...extraColumns].map((column) => ({
			key: column.key,
			label: column.label,
			width: column.width,
			priority: column.priority,
			align: column.align,
			mono: column.mono,
			accessor: column.value
		}))
	]);

	function metaLine(row: Row): string {
		return [...kind.columns, ...extraColumns]
			.filter((column) => (column.priority ?? 0) < 2)
			.map((column) => column.value(row))
			.filter(Boolean)
			.join(' · ');
	}
</script>

{#snippet primaryCell(row: Row)}
	{@const id = kind.getId(row)}
	<div class="min-w-0 flex-1">
		<PickerPrimaryCell
			{kind}
			{row}
			collision={collisions.get(id)}
			assigned={!!lockedIds?.has(id) && !!assignedIds?.has(id)}
			error={rowErrors?.[id] ?? null}
		/>
	</div>
{/snippet}

{#snippet cardCell(row: Row)}
	{@const id = kind.getId(row)}
	<PickerPrimaryCell
		{kind}
		{row}
		collision={collisions.get(id)}
		assigned={!!lockedIds?.has(id) && !!assignedIds?.has(id)}
		error={rowErrors?.[id] ?? null}
		compact
	/>
	{#if metaLine(row)}
		<div class="mt-1 truncate pl-[52px] font-mono text-xs text-fg-subtle">{metaLine(row)}</div>
	{/if}
{/snippet}

<DataTable
	{columns}
	{rows}
	getRowId={kind.getId}
	{selected}
	{onSelectedChange}
	rowSelect
	{selectionType}
	isLocked={lockedIds ? (row) => lockedIds.has(kind.getId(row)) : undefined}
	rowClass={(row) => (lockedIds?.has(kind.getId(row)) ? 'opacity-60' : '')}
	{headerState}
	{onHeaderToggle}
	{headerLabel}
	{onSelectAll}
	{onConfirm}
	{loading}
	{isFiltered}
	{emptyState}
	{filteredEmptyState}
	{rowActions}
	card={cardCell}
	{virtual}
	{rowHeight}
	{scrollMaxHeight}
	{onNearEnd}
	{ariaLabel}
	{forceNarrow}
	class={className}
/>
