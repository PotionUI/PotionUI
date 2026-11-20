<script lang="ts">
	import * as adminApi from '$lib/services/admin-api';
	import { Button, Badge, Spinner, EmptyState, LoadErrorState } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { isSystemRestrictedGroup } from '$lib/contentSafety/policy';
	import { DataTable, TablePager, pageCount, clampPage, type DataTableColumn } from '$lib/components/table';
	import type { GroupsFilters } from '../groupsFilters';
	import { useUsersDirectory } from './usersDirectory.svelte';

	let {
		rows,
		filters,
		selected = $bindable(),
		page = $bindable(),
		pageSize = $bindable(),
		onOpen,
		onAdd,
		onClearFilters
	}: {
		rows: adminApi.UserGroup[];
		filters: GroupsFilters;
		selected: Set<string>;
		page: number;
		pageSize: number;
		onOpen: (id: string) => void;
		onAdd: () => void;
		onClearFilters: () => void;
	} = $props();

	const directory = useUsersDirectory();

	const totalPages = $derived(pageCount(rows.length, pageSize));
	const pagedRows = $derived(rows.slice((clampPage(page, totalPages) - 1) * pageSize, clampPage(page, totalPages) * pageSize));

	const columns = $derived<DataTableColumn<adminApi.UserGroup>[]>([
		{ key: 'name', label: 'Name', width: 'minmax(180px,1.4fr)', priority: 0, accessor: (g: adminApi.UserGroup) => g.name },
		{ key: 'built_in', label: 'Built-in', width: '110px', priority: 0, cell: builtInCell },
		{ key: 'members', label: 'Members', width: '100px', priority: 1, align: 'right', mono: true, accessor: (g: adminApi.UserGroup) => g.member_count ?? 0 },
		{ key: 'access', label: 'Presets / LLMs / Models', width: '170px', priority: 2, cell: accessCell }
	]);
</script>

{#snippet builtInCell(group: adminApi.UserGroup)}
	{#if group.is_system}<Badge size="sm" variant="neutral">Built-in</Badge>{/if}
	{#if isSystemRestrictedGroup(group)}<Badge size="sm" variant="danger">Restricted</Badge>{/if}
{/snippet}

{#snippet accessCell(group: adminApi.UserGroup)}
	<Tooltip text="{group.preset_count ?? 0} presets · {group.llm_count ?? 0} LLM configs · {group.model_count ?? 0} models">
		<span class="font-mono text-xs tabular-nums text-fg-muted">{group.preset_count ?? 0} / {group.llm_count ?? 0} / {group.model_count ?? 0}</span>
	</Tooltip>
{/snippet}

{#if directory.loadingGroups || directory.loadingMemberships}
	<div class="flex h-full flex-col items-center justify-center">
		<Spinner size="lg" />
		<p class="text-sm text-fg-muted mt-4">Loading groups…</p>
	</div>
{:else if directory.groupsError && directory.groups.length === 0}
	<LoadErrorState message={directory.groupsError} onRetry={() => directory.loadGroups()} retrying={directory.loadingGroups} />
{:else}
	<div class="flex flex-col gap-3 p-4">
		<DataTable
			columns={columns}
			rows={pagedRows}
			getRowId={(g) => g.id}
			{selected}
			onSelectedChange={(next) => (selected = next)}
			onRowClick={(g) => onOpen(g.id)}
			isFiltered={!!filters.q}
		>
			{#snippet emptyState()}
				<EmptyState icon="group" title="No groups yet" description="Groups let you assign presets, LLMs, and models to many users at once." compact>
					{#snippet actions()}<Button size="sm" variant="secondary" icon="plus" onclick={onAdd}>Add group</Button>{/snippet}
				</EmptyState>
			{/snippet}
			{#snippet filteredEmptyState()}
				<EmptyState icon="search" title="No groups match your search" description="Try a different name or description." compact>
					{#snippet actions()}<Button size="sm" variant="secondary" onclick={onClearFilters}>Clear search</Button>{/snippet}
				</EmptyState>
			{/snippet}
			{#snippet card(g)}
				<div class="flex items-center gap-2">
					<p class="truncate text-sm font-semibold text-fg">{g.name}</p>
					{#if g.is_system}<Badge size="sm" variant="neutral">Built-in</Badge>{/if}
				</div>
				<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{g.member_count ?? 0} members</p>
			{/snippet}
		</DataTable>
		<TablePager
			page={clampPage(page, totalPages)}
			pageCount={totalPages}
			{pageSize}
			onPageChange={(p) => (page = p)}
			onPageSizeChange={(s) => {
				pageSize = s;
				page = 1;
			}}
		/>
	</div>
{/if}
