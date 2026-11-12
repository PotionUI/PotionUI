<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import type { User } from '$lib/stores/auth';
	import { EntityPicker } from '$lib/components/picker';
	import { presetsKind, groupsKind } from '$lib/components/picker/kinds';
	import Icon from '$lib/components/Icon.svelte';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { applySingle } from './assignments';
	import { useUsersDirectory } from './usersDirectory.svelte';
	import { deletableUserIds, pluralize } from './usersLibrary';

	let {
		rows,
		selected = $bindable(),
		currentUser,
		viewId,
		onCloseDetail
	}: {
		rows: User[];
		selected: Set<string>;
		currentUser: any;
		viewId: string | null;
		onCloseDetail: () => void;
	} = $props();

	const directory = useUsersDirectory();

	let showAddToGroup = $state(false);
	let showAssignPreset = $state(false);
	let deleting = $state(false);

	async function addToGroup(groupId: string) {
		try {
			const result = await adminApi.addUsersToGroup(groupId, Array.from(selected));
			if (!result.success) throw new Error(`Failed to add users to group: ${result.message || 'Unknown error'}`);
			await directory.loadMemberships();
			const groupName = directory.groups.find((g) => g.id === groupId)?.name ?? 'the group';
			toasts.success(`Added ${pluralize(selected.size, 'user')} to ${groupName}`);
			selected = new Set();
		} catch (error) {
			logger.error('Failed to add users to group:', error);
			throw error;
		}
	}

	async function assignPreset(presetId: string) {
		try {
			const result = await adminApi.assignPresetToUsers(presetId, Array.from(selected));
			if (!result.success) throw new Error(`Failed to assign preset: ${result.message || result.error || 'Unknown error'}`);
			const presetName = directory.allPresets.find((p) => p.id === presetId)?.name ?? 'the preset';
			toasts.success(`Assigned ${pluralize(selected.size, 'user')} to ${presetName}`);
			selected = new Set();
		} catch (error) {
			logger.error('Failed to assign preset to users:', error);
			throw error;
		}
	}

	async function bulkDelete() {
		const { deletableIds, skippedCount } = deletableUserIds(selected, currentUser?.id);
		if (deletableIds.length === 0) {
			if (skippedCount > 0) toasts.error('You cannot delete your own account.');
			return;
		}
		if (
			!(await confirmDialog({
				title: `Delete ${pluralize(deletableIds.length, 'user')}?`,
				message: `This action cannot be undone.${skippedCount ? ' Your own account was left out of this selection.' : ''}`,
				variant: 'danger'
			}))
		)
			return;
		deleting = true;
		try {
			await Promise.all(deletableIds.map((id) => adminApi.deleteUser(id)));
			const closeDetail = viewId ? deletableIds.includes(viewId) : false;
			selected = new Set();
			await directory.loadUsers();
			await directory.loadMemberships();
			if (closeDetail) onCloseDetail();
			toasts.success(`${pluralize(deletableIds.length, 'user')} deleted`);
		} catch (error) {
			logger.error('Failed to bulk delete users:', error);
			toasts.error('Failed to delete some users.');
		} finally {
			deleting = false;
		}
	}
</script>

<SelectionActionBar
	active={selected.size > 0}
	selectedCount={selected.size}
	totalCount={rows.length}
	onSelectAll={() => (selected = selectPage(selected, rows.map((u) => u.id)))}
	onClearSelection={() => (selected = clearAll())}
	onClose={() => (selected = clearAll())}
>
	<svelte:fragment slot="actionsBeforeCollection">
		<button
			class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
			onclick={() => (showAddToGroup = true)}
		>
			<Icon name="group" className="w-4 h-4" />
			Add to group
		</button>
		<button
			class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
			onclick={() => (showAssignPreset = true)}
		>
			<Icon name="layers" className="w-4 h-4" />
			Assign preset
		</button>
	</svelte:fragment>
	<svelte:fragment slot="pinnedActions">
		<button
			class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
			onclick={bulkDelete}
		>
			<Icon name="trash" className="w-4 h-4" />
			{deleting ? 'Deleting…' : 'Delete'}
		</button>
	</svelte:fragment>
</SelectionActionBar>

<EntityPicker
	kind={groupsKind}
	items={directory.groups}
	mode="single"
	isOpen={showAddToGroup}
	title="Add {pluralize(selected.size, 'user')} to a group"
	onApply={(diff) => applySingle(diff, addToGroup)}
	onClose={() => (showAddToGroup = false)}
/>

<EntityPicker
	kind={presetsKind}
	items={directory.allPresets}
	mode="single"
	isOpen={showAssignPreset}
	title="Assign {pluralize(selected.size, 'user')} to a preset"
	onApply={(diff) => applySingle(diff, assignPreset)}
	onClose={() => (showAssignPreset = false)}
/>
