<script lang="ts" generics="Row">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { DetailSection } from '$lib/components/detail';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { Badge, Button, EmptyState, IconButton, Input, LoadErrorState } from '$lib/components/ui';
	import { toasts } from '$lib/stores/toast';
	import { timeAgo } from '$lib/utils/relativeTime';
	import EntityPicker from './EntityPicker.svelte';
	import EntityRows from './EntityRows.svelte';
	import { buildCollisionIndex } from './pickFacts';
	import { selectFiltered } from './pickerState';
	import type { ApplyResult, EntityKind, PickerColumn, PickerDiff, RemoteSource } from './types';

	let {
		kind,
		label,
		assignedRows,
		pickerItems = [],
		assignedAt,
		remote,
		onApply,
		loading = false,
		error = null,
		onRetry,
		canEdit = true,
		addLabel,
		pickerTitle,
		pickerSubtitle = '',
		extraColumns = [],
		emptyAction,
		forceNarrow,
		ariaLabel
	}: {
		kind: EntityKind<Row>;
		label: string;
		assignedRows: readonly Row[];
		pickerItems?: readonly Row[];
		assignedAt?: Readonly<Record<string, string | undefined>>;
		remote?: RemoteSource<Row>;
		onApply: (diff: PickerDiff) => Promise<ApplyResult | void> | ApplyResult | void;
		loading?: boolean;
		error?: string | null;
		onRetry?: () => void;
		canEdit?: boolean;
		addLabel?: string;
		pickerTitle?: string;
		pickerSubtitle?: string;
		extraColumns?: readonly PickerColumn<Row>[];
		emptyAction?: Snippet;
		forceNarrow?: boolean;
		ariaLabel?: string;
	} = $props();

	let pickerOpen = $state(false);
	let q = $state('');
	let selected = $state<Set<string>>(new Set());
	let pending = $state<Set<string>>(new Set());

	const assignedIds = $derived(new Set(assignedRows.map(kind.getId)));
	const collisions = $derived(buildCollisionIndex(pickerItems.length > 0 ? pickerItems : assignedRows, kind));

	const visibleRows = $derived.by(() => {
		const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
		const base = terms.length === 0 ? [...assignedRows] : assignedRows.filter((row) => {
			const haystack = kind.searchText(row).toLowerCase();
			return terms.every((term) => haystack.includes(term));
		});
		const sort = kind.sorts.find((s) => s.value === kind.defaultSort);
		return sort ? base.sort(sort.compare) : base;
	});
	const visibleIds = $derived(visibleRows.map(kind.getId));

	const columns = $derived<PickerColumn<Row>[]>([
		...extraColumns,
		...(assignedAt
			? [
					{
						key: 'assigned_at',
						label: 'Assigned',
						width: '90px',
						priority: 1 as const,
						mono: true,
						value: (row: Row) => {
							const at = assignedAt[kind.getId(row)];
							return at ? timeAgo(at) : '';
						}
					}
				]
			: [])
	]);

	$effect(() => {
		const valid = new Set(assignedRows.map(kind.getId));
		const next = new Set([...selected].filter((id) => valid.has(id)));
		if (next.size !== selected.size) selected = next;
	});

	const resolvedAddLabel = $derived(addLabel ?? `Add ${kind.plural}`);

	async function run(diff: PickerDiff): Promise<ApplyResult | void> {
		const result = await onApply(diff);
		if (result && result.failed.length > 0) {
			toasts.error(`${result.failed.length} ${kind.plural} could not be changed`);
		}
		return result;
	}

	async function remove(ids: string[]) {
		pending = new Set([...pending, ...ids]);
		try {
			await run({ add: [], remove: ids });
			selected = new Set([...selected].filter((id) => !ids.includes(id)));
		} catch (caught) {
			toasts.error(caught instanceof Error && caught.message ? caught.message : `Could not remove ${kind.plural}`);
		} finally {
			pending = new Set([...pending].filter((id) => !ids.includes(id)));
		}
	}

	function nameOf(row: Row): string {
		return kind.getName(row);
	}
</script>

<DetailSection label={ariaLabel ?? label} padded={false}>
	{#snippet headerExtra()}
		<span class="font-mono text-xs tabular-nums text-fg-subtle" data-assigned-count>{assignedRows.length} assigned</span>
		{#if canEdit}
			<Button variant="primary" size="sm" icon="plus" onclick={() => (pickerOpen = true)}>{resolvedAddLabel}</Button>
		{/if}
	{/snippet}

	{#if error}
		<LoadErrorState message={error} onRetry={() => onRetry?.()} />
	{:else}
		{#if assignedRows.length > 0}
			<div class="border-b border-line px-4 py-3">
				<div class="relative">
					<Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" />
					<Input bind:value={q} type="search" class="pl-9" placeholder={`Filter assigned ${kind.plural}`} aria-label={`Filter assigned ${kind.plural}`} />
				</div>
			</div>
		{/if}
		<EntityRows
			{kind}
			rows={visibleRows}
			{collisions}
			{selected}
			onSelectedChange={(next) => (canEdit ? (selected = next) : undefined)}
			extraColumns={columns}
			{loading}
			isFiltered={q.trim() !== ''}
			virtual
			scrollMaxHeight="70vh"
			headerState={visibleIds.length > 0 && visibleIds.every((id) => selected.has(id)) ? 'all' : visibleIds.some((id) => selected.has(id)) ? 'some' : 'none'}
			onHeaderToggle={() =>
				(selected = visibleIds.every((id) => selected.has(id)) ? new Set() : selectFiltered(selected, visibleIds))}
			headerLabel={`Select all assigned ${kind.plural}`}
			onSelectAll={() => (selected = selectFiltered(selected, visibleIds))}
			ariaLabel={`Assigned ${kind.plural}`}
			{forceNarrow}
			class="!rounded-none !border-0"
		>
			{#snippet emptyState()}
				<EmptyState icon={kind.icon} title={`No ${kind.plural} assigned`} description={`Nothing is assigned here yet.`} compact>
					{#snippet actions()}
						{#if canEdit}
							<Button variant="primary" size="sm" icon="plus" onclick={() => (pickerOpen = true)}>{resolvedAddLabel}</Button>
						{/if}
						{@render emptyAction?.()}
					{/snippet}
				</EmptyState>
			{/snippet}
			{#snippet filteredEmptyState()}
				<EmptyState icon="search" title={`No assigned ${kind.plural} match`} description="Try a different search." compact />
			{/snippet}
			{#snippet rowActions(row)}
				{#if canEdit}
					<IconButton
						icon="close"
						size="sm"
						label={`Remove ${nameOf(row)}`}
						loading={pending.has(kind.getId(row))}
						class="text-danger hover:bg-danger/10"
						onclick={() => remove([kind.getId(row)])}
					/>
				{/if}
			{/snippet}
		</EntityRows>
	{/if}
</DetailSection>

{#if canEdit}
	<SelectionActionBar
		active={selected.size > 0}
		selectedCount={selected.size}
		totalCount={visibleIds.length}
		onSelectAll={() => (selected = selectFiltered(selected, visibleIds))}
		onClearSelection={() => (selected = new Set())}
		onClose={() => (selected = new Set())}
	>
		<svelte:fragment slot="actionsBeforeCollection">
			<Button variant="ghost" size="sm" icon="trash" class="text-danger" onclick={() => remove([...selected])}>
				Remove {selected.size}
			</Button>
		</svelte:fragment>
	</SelectionActionBar>

	<EntityPicker
		{kind}
		items={pickerItems}
		{assignedIds}
		isOpen={pickerOpen}
		title={pickerTitle ?? `Add ${kind.plural}`}
		subtitle={pickerSubtitle}
		{remote}
		{loading}
		onApply={run}
		onClose={() => (pickerOpen = false)}
		{extraColumns}
		{forceNarrow}
	/>
{/if}
