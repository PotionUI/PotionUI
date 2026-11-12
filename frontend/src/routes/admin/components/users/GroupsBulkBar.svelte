<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import Icon from '$lib/components/Icon.svelte';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { useUsersDirectory } from './usersDirectory.svelte';
	import { deletableGroupIds, pluralize } from './usersLibrary';

	let {
		rows,
		selected = $bindable(),
		viewId,
		onCloseDetail
	}: {
		rows: adminApi.UserGroup[];
		selected: Set<string>;
		viewId: string | null;
		onCloseDetail: () => void;
	} = $props();

	const directory = useUsersDirectory();

	let deleting = $state(false);

	async function bulkDelete() {
		const { deletableIds, skippedCount } = deletableGroupIds(directory.groups, selected);
		if (deletableIds.length === 0) {
			if (skippedCount > 0) toasts.error('Built-in groups cannot be deleted.');
			return;
		}
		if (
			!(await confirmDialog({
				title: `Delete ${pluralize(deletableIds.length, 'group')}?`,
				message: `This action cannot be undone.${skippedCount ? ' Built-in groups were left out of this selection.' : ''}`,
				variant: 'danger'
			}))
		)
			return;
		deleting = true;
		try {
			await Promise.all(deletableIds.map((id) => adminApi.deleteUserGroup(id)));
			const closeDetail = viewId ? deletableIds.includes(viewId) : false;
			selected = new Set();
			await directory.loadGroups();
			await directory.loadMemberships();
			if (closeDetail) onCloseDetail();
			toasts.success(`${pluralize(deletableIds.length, 'group')} deleted`);
		} catch (error) {
			logger.error('Failed to bulk delete groups:', error);
			toasts.error('Failed to delete some groups.');
		} finally {
			deleting = false;
		}
	}
</script>

<SelectionActionBar
	active={selected.size > 0}
	selectedCount={selected.size}
	totalCount={rows.length}
	onSelectAll={() => (selected = selectPage(selected, rows.map((g) => g.id)))}
	onClearSelection={() => (selected = clearAll())}
	onClose={() => (selected = clearAll())}
>
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
