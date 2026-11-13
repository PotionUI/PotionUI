<script lang="ts">
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { User } from '$lib/stores/auth';
	import { Button, Badge, Spinner, EmptyState, LoadErrorState } from '$lib/components/ui';
	import { DataTable, TablePager, pageCount, clampPage, type DataTableColumn } from '$lib/components/table';
	import { sourceLabel } from '$lib/plans/usage';
	import { usageColumnCell } from '../plans/usageColumnCell';
	import type { UsersFilters } from '../usersFilters';
	import { useUsersDirectory } from './usersDirectory.svelte';

	let {
		rows,
		filters,
		activeFilterCount,
		selected = $bindable(),
		page = $bindable(),
		pageSize = $bindable(),
		onOpen,
		onAdd,
		onClearFilters
	}: {
		rows: User[];
		filters: UsersFilters;
		activeFilterCount: number;
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
	const usageKinds = $derived(directory.planKindsInUse);

	const columns = $derived<DataTableColumn<User>[]>([
		{ key: 'username', label: 'Username', width: 'minmax(180px,1.4fr)', priority: 0 as const, accessor: (u: User) => u.username },
		{ key: 'account_type', label: 'Account type', width: '130px', priority: 0 as const, cell: accountTypeCell },
		{ key: 'email', label: 'Email', width: 'minmax(160px,1.6fr)', priority: 1 as const, mono: true, accessor: (u: User) => u.email },
		...(usageKinds.length
			? [
					{ key: 'plan', label: 'Plan', width: '120px', priority: 1 as const, accessor: (u: User) => directory.usageByUser.get(u.id)?.plan?.name ?? '—' },
					{ key: 'plan_source', label: 'Source', width: '90px', priority: 2 as const, accessor: (u: User) => sourceLabel(directory.usageByUser.get(u.id)?.source ?? 'none', directory.usageByUser.get(u.id)?.exempt) },
					...usageKinds.map((kind) => ({
						key: `usage:${kind.key}`,
						label: kind.short_label ?? kind.label,
						width: 'minmax(190px,1.2fr)',
						priority: 1 as const,
						cell: usageColumnCell<User>(kind, () => directory.usageByUser)
					}))
				]
			: []),
		{ key: 'groups', label: 'Groups', width: '90px', priority: 2 as const, align: 'right', mono: true, accessor: (u: User) => (directory.userGroupIds[u.id] || []).length },
		{ key: 'created', label: 'Created', width: '140px', priority: 2 as const, mono: true, accessor: (u: User) => (u.created_at ? timeAgo(u.created_at) : '—') },
		{ key: 'last_login', label: 'Last login', width: '140px', priority: 2 as const, mono: true, accessor: (u: User) => (u.last_login ? timeAgo(u.last_login) : '—') }
	]);
</script>

{#snippet accountTypeCell(user: User)}
	<Badge size="sm" variant={user.account_type === 'ADMIN' ? 'warning' : 'neutral'} class="font-mono uppercase">{user.account_type}</Badge>
	{#if directory.isUserRestricted(user.id)}<Badge size="sm" variant="danger">Restricted</Badge>{/if}
{/snippet}

{#if directory.loadingUsers || directory.loadingMemberships}
	<div class="flex h-full flex-col items-center justify-center">
		<Spinner size="lg" />
		<p class="text-sm text-fg-muted mt-4">Loading users…</p>
	</div>
{:else if directory.usersError && directory.users.length === 0}
	<LoadErrorState message={directory.usersError} onRetry={() => directory.loadUsers()} retrying={directory.loadingUsers} />
{:else}
	<div class="flex flex-col gap-3 p-4">
		<DataTable
			columns={columns}
			rows={pagedRows}
			getRowId={(u) => u.id}
			{selected}
			onSelectedChange={(next) => (selected = next)}
			onRowClick={(u) => onOpen(u.id)}
			isFiltered={!!filters.q || activeFilterCount > 0}
		>
			{#snippet emptyState()}
				<EmptyState icon="group" title="No users yet" description="Accounts show up here once people sign up or you add them yourself." compact>
					{#snippet actions()}<Button size="sm" variant="secondary" icon="plus" onclick={onAdd}>Add user</Button>{/snippet}
				</EmptyState>
			{/snippet}
			{#snippet filteredEmptyState()}
				<EmptyState icon="search" title="No users match your search" description="Try a different name or email." compact>
					{#snippet actions()}<Button size="sm" variant="secondary" onclick={onClearFilters}>Clear filters</Button>{/snippet}
				</EmptyState>
			{/snippet}
			{#snippet card(u)}
				<div class="flex items-center gap-2">
					<p class="truncate text-sm font-semibold text-fg">{u.username}</p>
					<Badge size="sm" variant={u.account_type === 'ADMIN' ? 'warning' : 'neutral'} class="font-mono uppercase">{u.account_type}</Badge>
					{#if directory.isUserRestricted(u.id)}<Badge size="sm" variant="danger">Restricted</Badge>{/if}
				</div>
				<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{u.email}</p>
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
