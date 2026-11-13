<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { page } from '$app/stores';
	import { createUrlState } from '$lib/utils/urlState';
	import { Button, EmptyState, LoadErrorState, Spinner } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import { PaneRow } from '$lib/components/pane';
	import { isUsageSort, sortByUsage, usageSortOptions } from '$lib/plans/usage';
	import PlansList from './plans/PlansList.svelte';
	import CreateGroupModal from './users/CreateGroupModal.svelte';
	import CreateUserModal from './users/CreateUserModal.svelte';
	import GroupDetail from './users/GroupDetail.svelte';
	import GroupsBulkBar from './users/GroupsBulkBar.svelte';
	import GroupsTable from './users/GroupsTable.svelte';
	import PlanDetailPane from './users/PlanDetailPane.svelte';
	import UserDetail from './users/UserDetail.svelte';
	import UsersBulkBar from './users/UsersBulkBar.svelte';
	import UsersTable from './users/UsersTable.svelte';
	import { provideUsersDirectory } from './users/usersDirectory.svelte';
	import {
		NEW_PLAN_ID,
		USERS_LIBRARY_SECTIONS,
		accountTypeCounts,
		usersSubViewFromParam,
		type UsersSubView
	} from './users/usersLibrary';
	import {
		applyPlansFilters,
		plansFiltersFromSearchParams,
		plansFiltersToSearchParams,
		type PlansFilters
	} from './plansFilters';
	import {
		USER_ACCOUNT_TYPE_OPTIONS,
		USERS_SORT_OPTIONS,
		applyUsersFilters,
		usersFiltersFromSearchParams,
		type UserAccountTypeFilter,
		type UserSortBy,
		type UsersFilters,
		usersFilters
	} from './usersFilters';
	import {
		GROUPS_SORT_OPTIONS,
		applyGroupsFilters,
		groupsFiltersFromSearchParams,
		groupsFiltersToSearchParams,
		type GroupSortBy,
		type GroupsFilters,
		groupsFilters
	} from './groupsFilters';

	let { currentUser }: { currentUser: any } = $props();

	type SubView = UsersSubView;

	const directory = provideUsersDirectory();

	const subView = $derived(usersSubViewFromParam($page.url.searchParams.get('view')));
	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);

	const currentUsersFilters = $derived(usersFiltersFromSearchParams($page.url.searchParams));
	const currentGroupsFilters = $derived(groupsFiltersFromSearchParams($page.url.searchParams));
	const plansFilters = $derived(plansFiltersFromSearchParams($page.url.searchParams));

	let createUserModal = $state<{ open: () => void }>();
	let createGroupModal = $state<{ open: () => void }>();

	let selectedUserIds = $state<Set<string>>(new Set());
	let selectedGroupIds = $state<Set<string>>(new Set());
	let usersPage = $state(1);
	let usersPageSize = $state(25);
	let groupsPage = $state(1);
	let groupsPageSize = $state(25);

	onMount(() => {
		void directory.bootstrap();
	});

	onDestroy(() => {
		usersUrl.cancel();
		groupsUrl.cancel();
		plansUrl.cancel();
	});

	function buildUrl(overrides: { view?: SubView; id?: string | null } = {}): string {
		const url = new URL($page.url);
		url.searchParams.set('tab', 'users');
		const nextView = overrides.view ?? subView;
		if (nextView !== 'users') url.searchParams.set('view', nextView);
		else url.searchParams.delete('view');
		const id = overrides.id !== undefined ? overrides.id : viewId;
		if (id) url.searchParams.set('id', id);
		else url.searchParams.delete('id');
		return `${url.pathname}?${url.searchParams.toString()}`;
	}

	const urlState = createUrlState({ href: buildUrl });

	function navigate(overrides: { view?: SubView; id?: string | null } = {}) {
		void urlState.go(overrides, { keepFocus: true, noScroll: true });
	}

	function setSubView(next: SubView) {
		if (next === subView) return;
		selectedUserIds = new Set();
		selectedGroupIds = new Set();
		usersPage = 1;
		groupsPage = 1;
		navigate({ view: next, id: null });
	}

	function filtersHref(clearKeys: string[], params: URLSearchParams): string {
		const url = new URL($page.url);
		for (const key of clearKeys) url.searchParams.delete(key);
		for (const [key, value] of params) url.searchParams.set(key, value);
		return `${url.pathname}?${url.searchParams.toString()}`;
	}

	const usersUrl = createUrlState<UsersFilters>({
		href: (next) => filtersHref(['q', 'account_type', 'sort_by'], usersFilters.toSearchParams(next))
	});
	const groupsUrl = createUrlState<GroupsFilters>({
		href: (next) => filtersHref(['group_q', 'group_sort_by'], groupsFiltersToSearchParams(next))
	});
	const plansUrl = createUrlState<PlansFilters>({
		href: (next) => filtersHref(['plan_q'], plansFiltersToSearchParams(next))
	});

	function updateUsersFilters(next: UsersFilters) {
		usersUrl.debounced(next, () => (usersPage = 1));
	}

	function updateGroupsFilters(next: GroupsFilters) {
		groupsUrl.debounced(next, () => (groupsPage = 1));
	}

	function updatePlansFilters(next: PlansFilters) {
		plansUrl.debounced(next);
	}

	const activeUser = $derived(subView === 'users' && viewId ? (directory.users.find((u) => u.id === viewId) ?? null) : null);
	const activeGroup = $derived(subView === 'groups' && viewId ? (directory.groups.find((g) => g.id === viewId) ?? null) : null);

	const typeCounts = $derived(accountTypeCounts(directory.users));
	const usersSortOptions = $derived([
		...USERS_SORT_OPTIONS,
		...(directory.planKindsInUse.length ? usageSortOptions(directory.planKindsInUse) : [])
	]);
	const filteredUsers = $derived.by(() => {
		const rows = applyUsersFilters(directory.users, currentUsersFilters);
		return isUsageSort(currentUsersFilters.sortBy) ? sortByUsage(rows, directory.usageByUser, currentUsersFilters.sortBy) : rows;
	});
	const filteredGroups = $derived(applyGroupsFilters(directory.groups, currentGroupsFilters));
	const filteredPlans = $derived(applyPlansFilters(directory.plans, plansFilters));

	const userFilterChips = $derived(usersFilters.chips(currentUsersFilters));
	const activeUserFilterCount = $derived(usersFilters.activeCount(currentUsersFilters));
	const groupFilterChips = $derived(groupsFilters.chips(currentGroupsFilters));

	function clearUserFilters() {
		updateUsersFilters(usersFilters.clearAll(currentUsersFilters));
	}
	function removeUserFilterChip(key: string) {
		updateUsersFilters(usersFilters.clearChip(currentUsersFilters, key));
	}
	function toggleAccountTypeFilter(type: UserAccountTypeFilter) {
		updateUsersFilters({ ...currentUsersFilters, accountType: currentUsersFilters.accountType === type ? 'all' : type });
	}
	function clearGroupFilters() {
		updateGroupsFilters(groupsFilters.clearAll(currentGroupsFilters));
	}
	function removeGroupFilterChip(key: string) {
		updateGroupsFilters(groupsFilters.clearChip(currentGroupsFilters, key));
	}

	const closeDetail = () => navigate({ id: null });
</script>

<LibraryShell
	title="Users"
	persistKey="admin-users-library"
	heightClass="h-full"
	sections={USERS_LIBRARY_SECTIONS}
	section={subView}
	onSelectSection={setSubView}
	sectionCounts={{ users: directory.users.length, groups: directory.groups.length, plans: directory.plans.length }}
	count={subView === 'users' ? filteredUsers.length : subView === 'groups' ? filteredGroups.length : filteredPlans.length}
	{detailOpen}
	filterChips={subView === 'users' ? userFilterChips : subView === 'groups' ? groupFilterChips : []}
	onRemoveChip={(key) => (subView === 'users' ? removeUserFilterChip(key) : removeGroupFilterChip(key))}
	onClearFilters={() => (subView === 'users' ? clearUserFilters() : clearGroupFilters())}
	loadedCount={subView === 'users' ? filteredUsers.length : subView === 'groups' ? filteredGroups.length : filteredPlans.length}
	total={subView === 'users' ? directory.users.length : subView === 'groups' ? directory.groups.length : directory.plans.length}
>
	{#snippet sidebarTree()}
		{#if subView === 'users'}
			<div class="border-t border-line">
				<div class="px-3 pb-1 pt-2 font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">By account type</div>
				<div class="space-y-0.5 p-2 pt-0">
					<PaneRow
						title="Administrators"
						count={typeCounts.ADMIN}
						selected={currentUsersFilters.accountType === 'ADMIN'}
						onclick={() => toggleAccountTypeFilter('ADMIN')}
					/>
					<PaneRow
						title="Regular users"
						count={typeCounts.USER}
						selected={currentUsersFilters.accountType === 'USER'}
						onclick={() => toggleAccountTypeFilter('USER')}
					/>
				</div>
			</div>
		{/if}
	{/snippet}

	{#snippet toolbar()}
		{#if subView === 'users'}
			<LibraryFilterBar
				q={currentUsersFilters.q}
				onQueryChange={(value) => updateUsersFilters({ ...currentUsersFilters, q: value })}
				searchPlaceholder="Search by name or email…"
				sortBy={currentUsersFilters.sortBy}
				sortOptions={usersSortOptions}
				onSortChange={(value) => updateUsersFilters({ ...currentUsersFilters, sortBy: value as UserSortBy })}
				filterCount={activeUserFilterCount}
			>
				{#snippet popover(close: () => void)}
					<FilterPopoverFrame label="User filters" onClearAll={clearUserFilters} onClose={close}>
						<SegmentedFilterGroup
							label="Account type"
							options={USER_ACCOUNT_TYPE_OPTIONS}
							value={currentUsersFilters.accountType}
							onChange={(accountType: UserAccountTypeFilter) => updateUsersFilters({ ...currentUsersFilters, accountType })}
						/>
					</FilterPopoverFrame>
				{/snippet}
			</LibraryFilterBar>
		{:else if subView === 'plans'}
			<LibraryFilterBar
				q={plansFilters.q}
				onQueryChange={(value) => updatePlansFilters({ ...plansFilters, q: value })}
				searchPlaceholder="Search plans…"
			/>
		{:else}
			<LibraryFilterBar
				q={currentGroupsFilters.q}
				onQueryChange={(value) => updateGroupsFilters({ ...currentGroupsFilters, q: value })}
				searchPlaceholder="Search by name or description…"
				sortBy={currentGroupsFilters.sortBy}
				sortOptions={GROUPS_SORT_OPTIONS}
				onSortChange={(value) => updateGroupsFilters({ ...currentGroupsFilters, sortBy: value as GroupSortBy })}
			/>
		{/if}
	{/snippet}

	{#snippet primary()}
		{#if subView === 'users'}
			<Button variant="primary" size="sm" icon="plus" onclick={() => createUserModal?.open()}>Add user</Button>
		{:else if subView === 'plans'}
			<Button variant="primary" size="sm" icon="plus" onclick={() => navigate({ view: 'plans', id: NEW_PLAN_ID })}>New plan</Button>
		{:else}
			<Button variant="primary" size="sm" icon="plus" onclick={() => createGroupModal?.open()}>Add group</Button>
		{/if}
	{/snippet}

	{#if detailOpen}
		{#if subView === 'users'}
			{#if !activeUser}
				<div class="flex h-full items-center justify-center">
					{#if directory.loadingUsers}
						<Spinner size="lg" />
					{:else}
						<EmptyState title="User not found" description="This user may have been removed." icon="group" compact>
							{#snippet actions()}<Button size="sm" variant="secondary" onclick={closeDetail}>Back to users</Button>{/snippet}
						</EmptyState>
					{/if}
				</div>
			{:else}
				{#key activeUser.id}
					<UserDetail user={activeUser} {currentUser} onBack={closeDetail} />
				{/key}
			{/if}
		{:else if subView === 'plans'}
			{#key viewId}
				<PlanDetailPane
					planId={viewId ?? ''}
					onBack={closeDetail}
					onCreated={(id) => navigate({ view: 'plans', id })}
				/>
			{/key}
		{:else if !activeGroup}
			<div class="flex h-full items-center justify-center">
				{#if directory.loadingGroups}
					<Spinner size="lg" />
				{:else}
					<EmptyState title="Group not found" description="This group may have been removed." icon="group" compact>
						{#snippet actions()}<Button size="sm" variant="secondary" onclick={closeDetail}>Back to groups</Button>{/snippet}
					</EmptyState>
				{/if}
			</div>
		{:else}
			{#key activeGroup.id}
				<GroupDetail group={activeGroup} onBack={closeDetail} />
			{/key}
		{/if}
	{:else if subView === 'plans'}
		{#if directory.loadingPlans}
			<div class="flex h-full flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading plans…</p>
			</div>
		{:else if directory.plansError && directory.plans.length === 0}
			<LoadErrorState message={directory.plansError} onRetry={() => directory.loadPlans()} retrying={directory.loadingPlans} />
		{:else}
			<PlansList
				plans={filteredPlans}
				kinds={directory.planKinds}
				settings={directory.plansSettings}
				onOpen={(plan) => navigate({ view: 'plans', id: plan.id })}
				onSettings={(patch) => directory.savePlansSettings(patch)}
			/>
		{/if}
	{:else if subView === 'users'}
		<UsersTable
			rows={filteredUsers}
			filters={currentUsersFilters}
			activeFilterCount={activeUserFilterCount}
			bind:selected={selectedUserIds}
			bind:page={usersPage}
			bind:pageSize={usersPageSize}
			onOpen={(id) => navigate({ id })}
			onAdd={() => createUserModal?.open()}
			onClearFilters={clearUserFilters}
		/>
	{:else}
		<GroupsTable
			rows={filteredGroups}
			filters={currentGroupsFilters}
			bind:selected={selectedGroupIds}
			bind:page={groupsPage}
			bind:pageSize={groupsPageSize}
			onOpen={(id) => navigate({ id })}
			onAdd={() => createGroupModal?.open()}
			onClearFilters={clearGroupFilters}
		/>
	{/if}
</LibraryShell>

{#if subView === 'users'}
	<UsersBulkBar rows={filteredUsers} bind:selected={selectedUserIds} {currentUser} {viewId} onCloseDetail={closeDetail} />
{:else if subView === 'groups'}
	<GroupsBulkBar rows={filteredGroups} bind:selected={selectedGroupIds} {viewId} onCloseDetail={closeDetail} />
{/if}

<CreateUserModal bind:this={createUserModal} onCreated={(id) => id && navigate({ view: 'users', id })} />
<CreateGroupModal bind:this={createGroupModal} onCreated={(id) => id && navigate({ view: 'groups', id })} />
