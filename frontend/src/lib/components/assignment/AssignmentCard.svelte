<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import { logger } from '$lib/utils/logger';
	import { SegmentedControl } from '$lib/components/ui';
	import { AssignedList, applyDiff } from '$lib/components/picker';
	import { groupsKind, usersKind } from '$lib/components/picker/kinds';
	import type { ApplyResult, PickerDiff } from '$lib/components/picker';
	import type { PickerUser } from '$lib/components/picker/kinds';
	import type { APIResponse } from '$lib/types/api';
	import type { AssignmentAdapter } from './types';

	export let adapter: AssignmentAdapter;
	export let resourceName: string;
	export let resourceKey: string;

	type AccessView = 'users' | 'groups';

	const dispatch = createEventDispatcher<{
		changed: { userCount: number; groupCount: number };
	}>();

	let accessView: AccessView = 'users';
	let allUsers: PickerUser[] = [];
	let allGroups: adminApi.UserGroup[] = [];
	let assignedUserIds = new Set<string>();
	let assignedGroupIds = new Set<string>();
	let loading = true;
	let loadError = '';
	let loadedForKey = '';
	let requestVersion = 0;

	$: assignedUsers = allUsers.filter((user) => assignedUserIds.has(user.id));
	$: assignedGroups = allGroups.filter((group) => assignedGroupIds.has(group.id));

	$: if (resourceKey && resourceKey !== loadedForKey) {
		loadedForKey = resourceKey;
		loadAccess();
	}

	function errorMessage(response: { message?: string } | null | undefined, fallback: string) {
		return response?.message || fallback;
	}

	function ensureOk(response: APIResponse, fallback: string) {
		if (!response.success) throw new Error(errorMessage(response, fallback));
	}

	async function loadAccess() {
		const activeAdapter = adapter;
		const activeKey = resourceKey;
		const version = ++requestVersion;
		loading = true;
		loadError = '';

		try {
			const [usersResponse, groupsResponse, state] = await Promise.all([
				adminApi.getUsers(),
				adminApi.getUserGroups(),
				activeAdapter.loadState()
			]);

			if (!usersResponse.success) throw new Error(errorMessage(usersResponse, 'Could not load users'));
			if (!groupsResponse.success) throw new Error(errorMessage(groupsResponse, 'Could not load user groups'));
			if (version !== requestVersion || activeKey !== resourceKey) return;

			allUsers = usersResponse.data || [];
			allGroups = groupsResponse.data || [];
			assignedUserIds = state.userIds;
			assignedGroupIds = state.groupIds;
		} catch (error) {
			if (version !== requestVersion || activeKey !== resourceKey) return;
			logger.error('Failed to load assignment state:', error);
			loadError = error instanceof Error ? error.message : 'Could not load access settings';
		} finally {
			if (version === requestVersion && activeKey === resourceKey) loading = false;
		}
	}

	async function refreshState(activeKey: string) {
		const state = await adapter.loadState();
		if (activeKey !== resourceKey) return;
		assignedUserIds = state.userIds;
		assignedGroupIds = state.groupIds;
		dispatch('changed', { userCount: state.userIds.size, groupCount: state.groupIds.size });
	}

	async function applyUsers(diff: PickerDiff): Promise<ApplyResult> {
		const activeKey = resourceKey;
		const result = await applyDiff(diff, {
			assign: async (id) => ensureOk(await adapter.assignUser(id), 'The user assignment could not be updated'),
			unassign: async (id) => ensureOk(await adapter.unassignUser(id), 'The user assignment could not be updated')
		});
		await refreshState(activeKey);
		return result;
	}

	async function applyGroups(diff: PickerDiff): Promise<ApplyResult> {
		const activeKey = resourceKey;
		const result = await applyDiff(diff, {
			assign: async (id) => ensureOk(await adapter.assignGroup(id), 'The group assignment could not be updated'),
			unassign: async (id) => ensureOk(await adapter.unassignGroup(id), 'The group assignment could not be updated')
		});
		await refreshState(activeKey);
		return result;
	}
</script>

<div data-testid="assignment-card" class="flex flex-col gap-3">
	<SegmentedControl
		items={[
			{ id: 'users', label: 'Users', icon: 'user', count: assignedUserIds.size },
			{ id: 'groups', label: 'Groups', icon: 'group', count: assignedGroupIds.size }
		]}
		selected={accessView}
		onSelect={(id) => (accessView = id as AccessView)}
		ariaLabel="Access type"
	/>

	{#if accessView === 'users'}
		<AssignedList
			kind={usersKind}
			label="Access"
			assignedRows={assignedUsers}
			pickerItems={allUsers}
			onApply={applyUsers}
			{loading}
			error={loadError || null}
			onRetry={loadAccess}
			addLabel="Add users"
			pickerTitle={`Add users to ${resourceName}`}
			pickerSubtitle={`Selected users can use ${resourceName} directly.`}
		/>
	{:else}
		<AssignedList
			kind={groupsKind}
			label="Access"
			assignedRows={assignedGroups}
			pickerItems={allGroups}
			onApply={applyGroups}
			{loading}
			error={loadError || null}
			onRetry={loadAccess}
			addLabel="Add groups"
			pickerTitle={`Add groups to ${resourceName}`}
			pickerSubtitle={`Every member of a selected group can use ${resourceName}.`}
		/>
	{/if}
</div>
