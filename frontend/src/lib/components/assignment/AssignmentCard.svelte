<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import type { User } from '$lib/stores/auth';
	import { logger } from '$lib/utils/logger';
	import { toasts } from '$lib/stores/toast';
	import { Badge, Button, Input, Spinner, EmptyState, SegmentedControl } from '$lib/components/ui';
	import { toGroupAccessRows, toUserAccessRows } from './accessRows';
	import type { AssignmentAdapter } from './types';

	export let adapter: AssignmentAdapter;
	export let resourceName: string;
	/** Identifies which resource `adapter` targets - a primitive so reload only
	    fires when it actually changes, not whenever the parent re-renders and
	    hands down a freshly-constructed (but equivalent) adapter object. */
	export let resourceKey: string;

	type AccessView = 'users' | 'groups';

	const dispatch = createEventDispatcher<{
		changed: { userCount: number; groupCount: number };
	}>();

	let accessView: AccessView = 'users';
	let searchQuery = '';
	let allUsers: User[] = [];
	let allGroups: adminApi.UserGroup[] = [];
	let assignedUserIds = new Set<string>();
	let assignedGroupIds = new Set<string>();
	let loading = true;
	let loadError = '';
	let processingKeys = new Set<string>();
	let loadedForKey = '';
	let requestVersion = 0;

	$: rows =
		accessView === 'users'
			? toUserAccessRows(allUsers, assignedUserIds, searchQuery)
			: toGroupAccessRows(allGroups, assignedGroupIds, searchQuery);
	$: total = accessView === 'users' ? allUsers.length : allGroups.length;
	$: toggleRow = accessView === 'users' ? toggleUser : toggleGroup;
	$: view =
		accessView === 'users'
			? {
					searchPlaceholder: 'Find a user…',
					searchLabel: 'Search users',
					heading: 'Direct user access',
					description: `Add individual users who should see ${resourceName} directly.`,
					addLabel: 'Add user',
					emptyIcon: 'user',
					noMatchTitle: 'No users match your search',
					noneTitle: 'No users yet',
					noneDescription: 'Create a user from the Users admin tab first.'
				}
			: {
					searchPlaceholder: 'Find a group…',
					searchLabel: 'Search groups',
					heading: 'User group access',
					description: `Every member of an assigned group can use ${resourceName}.`,
					addLabel: 'Add group',
					emptyIcon: 'group',
					noMatchTitle: 'No groups match your search',
					noneTitle: 'No user groups yet',
					noneDescription: 'Create a group from the User Groups admin tab first.'
				};

	$: if (resourceKey && resourceKey !== loadedForKey) {
		loadedForKey = resourceKey;
		searchQuery = '';
		loadAccess();
	}

	function errorMessage(response: { message?: string } | null | undefined, fallback: string) {
		return response?.message || fallback;
	}

	async function loadAccess() {
		const activeAdapter = adapter;
		const activeKey = resourceKey;
		const version = ++requestVersion;
		loading = true;
		loadError = '';
		processingKeys = new Set();

		try {
			const [usersResponse, groupsResponse, state] = await Promise.all([
				adminApi.getUsers(),
				adminApi.getUserGroups(),
				activeAdapter.loadState()
			]);

			if (!usersResponse.success) {
				throw new Error(errorMessage(usersResponse, 'Could not load users'));
			}
			if (!groupsResponse.success) {
				throw new Error(errorMessage(groupsResponse, 'Could not load user groups'));
			}
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

	function notifyChanged() {
		dispatch('changed', {
			userCount: assignedUserIds.size,
			groupCount: assignedGroupIds.size
		});
	}

	// Granting/revoking commits immediately, one API call per toggle — there's no
	// draft state to save here, each row's control IS the action.
	async function toggleUser(userId: string) {
		const activeAdapter = adapter;
		const activeKey = resourceKey;
		const assigned = assignedUserIds.has(userId);
		const key = `user:${userId}`;
		processingKeys = new Set(processingKeys).add(key);
		try {
			const response = assigned
				? await activeAdapter.unassignUser(userId)
				: await activeAdapter.assignUser(userId);
			if (!response.success) {
				throw new Error(errorMessage(response, 'The user assignment could not be updated'));
			}
			if (activeKey !== resourceKey) return;

			const next = new Set(assignedUserIds);
			if (assigned) next.delete(userId);
			else next.add(userId);
			assignedUserIds = next;
			notifyChanged();
		} catch (error) {
			logger.error('Failed to update user assignment:', error);
			toasts.error(error instanceof Error ? error.message : 'Failed to update user access');
		} finally {
			const nextProcessing = new Set(processingKeys);
			nextProcessing.delete(key);
			processingKeys = nextProcessing;
		}
	}

	async function toggleGroup(groupId: string) {
		const activeAdapter = adapter;
		const activeKey = resourceKey;
		const assigned = assignedGroupIds.has(groupId);
		const key = `group:${groupId}`;
		processingKeys = new Set(processingKeys).add(key);
		try {
			const response = assigned
				? await activeAdapter.unassignGroup(groupId)
				: await activeAdapter.assignGroup(groupId);
			if (!response.success) {
				throw new Error(errorMessage(response, 'The group assignment could not be updated'));
			}
			if (activeKey !== resourceKey) return;

			const next = new Set(assignedGroupIds);
			if (assigned) next.delete(groupId);
			else next.add(groupId);
			assignedGroupIds = next;
			notifyChanged();
		} catch (error) {
			logger.error('Failed to update group assignment:', error);
			toasts.error(error instanceof Error ? error.message : 'Failed to update group access');
		} finally {
			const nextProcessing = new Set(processingKeys);
			nextProcessing.delete(key);
			processingKeys = nextProcessing;
		}
	}
</script>

<div class="space-y-4" data-testid="assignment-card">
	<div class="flex flex-col sm:flex-row sm:items-center gap-3">
		<SegmentedControl
			items={[
				{ id: 'users', label: 'Users', icon: 'user', count: assignedUserIds.size },
				{ id: 'groups', label: 'Groups', icon: 'group', count: assignedGroupIds.size }
			]}
			selected={accessView}
			onSelect={(id) => (accessView = id as AccessView)}
			ariaLabel="Access type"
		/>
		<div class="sm:ml-auto sm:w-72">
			<Input
				bind:value={searchQuery}
				type="search"
				placeholder={view.searchPlaceholder}
				aria-label={view.searchLabel}
			/>
		</div>
	</div>

	<div class="rounded-lg border border-line bg-surface-1 overflow-hidden">
		<div class="px-4 py-3 border-b border-line bg-surface-2/60">
			<p class="text-sm font-medium text-fg">{view.heading}</p>
			<p class="text-xs text-fg-muted mt-0.5">
				{view.description}
				<span class="font-mono text-2xs text-fg-subtle">{rows.length} of {total} shown</span>
			</p>
		</div>

		{#if loading}
			<div class="flex flex-col items-center justify-center py-14">
				<Spinner size="md" />
				<p class="text-sm text-fg-muted mt-3">Loading access settings…</p>
			</div>
		{:else if loadError}
			<div class="p-5">
				<EmptyState title="Access settings are unavailable" description={loadError} icon="warning" compact>
					{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={() => loadAccess()}>Retry</Button>{/snippet}
				</EmptyState>
			</div>
		{:else if rows.length === 0}
			<EmptyState
				icon={view.emptyIcon}
				title={total ? view.noMatchTitle : view.noneTitle}
				description={total ? 'Try a different search term.' : view.noneDescription}
				compact
			/>
		{:else}
			<div class="divide-y divide-line/70">
				{#each rows as row (row.id)}
					<div class="flex items-center gap-3 px-4 py-3" data-testid="assignment-row" data-key={row.key}>
						<span
							class="w-2 h-2 rounded-full flex-shrink-0 {row.assigned ? 'bg-success-solid' : 'bg-line-strong'}"
							title={row.assigned ? 'Has access' : 'No access'}
						></span>
						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-2 min-w-0">
								<p class="text-sm font-medium text-fg truncate">{row.title}</p>
								{#if row.badge}<Badge variant={row.badge.variant} size="sm">{row.badge.text}</Badge>{/if}
								{#if row.assigned}<Badge variant="success" size="sm" dot>has access</Badge>{/if}
							</div>
							{#if row.subtitle}<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{row.subtitle}</p>{/if}
						</div>
						<Button
							variant={row.assigned ? 'ghost' : 'secondary'}
							size="sm"
							icon={row.assigned ? 'close' : 'plus'}
							class={row.assigned ? 'text-danger hover:text-danger hover:bg-danger/10' : ''}
							loading={processingKeys.has(row.key)}
							disabled={processingKeys.has(row.key)}
							onclick={() => toggleRow(row.id)}
						>
							{row.assigned ? 'Remove' : view.addLabel}
						</Button>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
