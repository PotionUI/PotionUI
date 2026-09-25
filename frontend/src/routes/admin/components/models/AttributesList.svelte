<script lang="ts">
	import { Badge, Button, EmptyState, Spinner } from '$lib/components/ui';
	import { DataTable, type DataTableColumn } from '$lib/components/table';
	import type { AttributeDefinition } from '$lib/types/models';

	let {
		definitions,
		loading = false,
		error = null,
		isFiltered = false,
		selected = $bindable(new Set<string>()),
		onOpen,
		onRetry,
		onClearFilters
	}: {
		definitions: readonly AttributeDefinition[];
		loading?: boolean;
		error?: string | null;
		isFiltered?: boolean;
		selected?: Set<string>;
		onOpen: (id: string) => void;
		onRetry: () => void;
		onClearFilters: () => void;
	} = $props();

	function appliesToLabel(definition: AttributeDefinition): string {
		return definition.model_types.length ? definition.model_types.join(', ') : 'All types';
	}
</script>

{#snippet boolCell(value: boolean)}
	<span class="font-mono text-xs {value ? 'text-fg' : 'text-fg-subtle'}">{value ? 'Yes' : '—'}</span>
{/snippet}

{#snippet perUserCell(definition: AttributeDefinition)}
	{@render boolCell(definition.per_user)}
{/snippet}

{#snippet adminOnlyCell(definition: AttributeDefinition)}
	{@render boolCell(definition.admin_only)}
{/snippet}

{#snippet builtInCell(definition: AttributeDefinition)}
	{#if definition.system}
		<Badge variant="info" size="sm">Built-in</Badge>
	{:else}
		<span class="font-mono text-xs text-fg-subtle">—</span>
	{/if}
{/snippet}

{#snippet card(definition: AttributeDefinition)}
	<div class="truncate text-sm font-semibold text-fg">{definition.label}</div>
	<div class="font-mono text-xs text-fg-subtle">{definition.key} · {definition.field_type}</div>
{/snippet}

{#snippet emptyState()}
	<EmptyState
		icon="settings"
		title="No attributes defined yet"
		description="Attributes are the per-model-type fields shown in a model's details — trigger words, LoRA strength, and anything else you declare."
		compact
	/>
{/snippet}

{#snippet filteredEmptyState()}
	<EmptyState title="No attributes match your search" description="Try a different key or label." icon="search" compact>
		{#snippet actions()}<Button variant="ghost" size="sm" onclick={onClearFilters}>Clear filters</Button>{/snippet}
	</EmptyState>
{/snippet}

{#if error}
	<div class="flex h-full items-center justify-center p-5">
		<EmptyState title="Error loading attributes" description={error} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={onRetry}>Try again</Button>{/snippet}
		</EmptyState>
	</div>
{:else}
	<DataTable
		columns={[
			{ key: 'label', label: 'Label', width: 'minmax(160px,1.4fr)', accessor: (d: AttributeDefinition) => d.label },
			{ key: 'key', label: 'Key', width: 'minmax(120px,1fr)', mono: true, accessor: (d: AttributeDefinition) => d.key },
			{ key: 'field_type', label: 'Type', width: '110px', accessor: (d: AttributeDefinition) => d.field_type.toUpperCase() },
			{ key: 'applies_to', label: 'Applies to', width: 'minmax(140px,1.2fr)', priority: 1, accessor: appliesToLabel },
			{ key: 'per_user', label: 'Per-user', width: '90px', priority: 1, cell: perUserCell },
			{ key: 'admin_only', label: 'Admin-only', width: '100px', priority: 2, cell: adminOnlyCell },
			{ key: 'system', label: 'Built-in', width: '100px', priority: 1, cell: builtInCell }
		] satisfies DataTableColumn<AttributeDefinition>[]}
		rows={definitions}
		getRowId={(d) => d.id}
		onRowClick={(d) => onOpen(d.id)}
		{selected}
		onSelectedChange={(next) => (selected = next)}
		{loading}
		{isFiltered}
		{emptyState}
		{filteredEmptyState}
		{card}
	/>
{/if}
