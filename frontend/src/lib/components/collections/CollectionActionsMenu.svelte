<script lang="ts" generics="T extends CollectionLike">
	import Icon from '$lib/components/Icon.svelte';
	import { Menu } from '$lib/components/ui';
	import { descendantIds } from '$lib/components/pane';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { showDeleteError } from '$lib/elixirs/openBrew';
	import { runDeleteEntries } from './deleteEntries';
	import type { OrganizeCollectionScope } from '$lib/types/organize';
	import CollectionRulesLink from './CollectionRulesLink.svelte';
	import CollectionNameDialog from './CollectionNameDialog.svelte';
	import CollectionMoveDialog from './CollectionMoveDialog.svelte';
	import { MENU_ITEM_CLASS } from './sidebar';
	import type { CollectionLike, MutationResult, TreeActions } from './types';

	let {
		item,
		collections,
		treeActions,
		rulesScope,
		variant = 'row',
		onOpenChange,
		onChildCreated
	}: {
		item: T;
		collections: T[];
		treeActions: TreeActions;
		rulesScope?: OrganizeCollectionScope;
		variant?: 'row' | 'toolbar';
		onOpenChange?: (open: boolean) => void;
		onChildCreated?: (parentId: string) => void;
	} = $props();

	let nameMode = $state<'create' | 'rename' | null>(null);
	let moving = $state(false);

	let moveBlocked = $derived.by(() => {
		const blocked = descendantIds(collections, item.id);
		if (item.parent_id) blocked.add(item.parent_id);
		return blocked;
	});

	function run(close: (restoreFocus?: boolean) => void, action: () => void) {
		close();
		action();
	}

	async function submitName(name: string): Promise<MutationResult> {
		if (nameMode === 'rename') {
			return name === item.name ? { success: true } : treeActions.onRename(item.id, name);
		}
		const result = await treeActions.onCreate(name, item.id);
		if (result.success) onChildCreated?.(item.id);
		return result;
	}

	function deleteEntries() {
		const entries = treeActions.entries;
		if (!entries) return;
		const hasSubcollections = collections.some((collection) => collection.parent_id === item.id);
		void runDeleteEntries({ id: item.id, name: item.name, hasSubcollections }, entries, {
			confirm: confirmDialog,
			success: (message) => toasts.success(message),
			info: (message) => toasts.info(message),
			error: (message) => toasts.error(message),
			failed: (error) => showDeleteError(error, 'Failed to delete entries.')
		});
	}

	async function remove() {
		const confirmed = await confirmDialog({
			title: 'Delete collection?',
			message: `“${item.name}” and any subcollections will be deleted. Items stay in All; only the collections and their memberships are removed.`,
			variant: 'danger',
			confirmLabel: 'Delete collection'
		});
		if (!confirmed) return;
		try {
			const result = await treeActions.onDelete(item.id, descendantIds(collections, item.id));
			if (!result.success) toasts.error(result.message ?? result.error ?? 'Failed to delete.');
		} catch (e) {
			logger.error('Delete failed:', getErrorMessage(e));
			toasts.error('Failed to delete.');
		}
	}
</script>

{#snippet rowTrigger({ open: isOpen, toggle }: { open: boolean; toggle: () => void })}
	<button
		type="button"
		class="flex h-[30px] w-[30px] items-center justify-center rounded text-fg-muted hover:bg-surface-3 hover:text-fg max-md:h-[38px] max-md:w-8 [@media(hover:none)]:h-[38px] [@media(hover:none)]:w-8"
		aria-label="{item.name} actions"
		aria-haspopup="menu"
		aria-expanded={isOpen}
		onclick={(e) => {
			e.stopPropagation();
			toggle();
		}}
		onkeydown={(e) => e.stopPropagation()}
	>
		<Icon name="more" className="h-4 w-4" />
	</button>
{/snippet}

<Menu
	label={variant === 'row' ? `${item.name} actions` : 'Collection actions'}
	menuLabel="{item.name} actions"
	icon="folder"
	size="md"
	width={224}
	align={variant === 'row' ? 'left' : 'right'}
	trigger={variant === 'row' ? rowTrigger : undefined}
	onopen={() => onOpenChange?.(true)}
	onclose={() => onOpenChange?.(false)}
>
	{#snippet children({ close })}
		<p class="break-words px-2.5 py-[9px] text-sm font-medium text-fg-muted" data-testid="collection-menu-title">
			{item.name}
		</p>
		<button type="button" role="menuitem" class="{MENU_ITEM_CLASS} text-fg" onclick={() => run(close, () => (nameMode = 'create'))}>
			<Icon name="folder-plus" className="h-4 w-4 flex-shrink-0 text-fg-muted" strokeWidth={1.75} />
			New subcollection
		</button>
		<button type="button" role="menuitem" class="{MENU_ITEM_CLASS} text-fg" onclick={() => run(close, () => (nameMode = 'rename'))}>
			<Icon name="pencil" className="h-4 w-4 flex-shrink-0 text-fg-muted" strokeWidth={1.75} />
			Rename…
		</button>
		<button type="button" role="menuitem" class="{MENU_ITEM_CLASS} text-fg" onclick={() => run(close, () => (moving = true))}>
			<Icon name="arrow-right" className="h-4 w-4 flex-shrink-0 text-fg-muted" strokeWidth={1.75} />
			Move to…
		</button>
		{#if rulesScope}
			<CollectionRulesLink scope={rulesScope} collectionId={item.id} />
		{/if}
		<div class="mx-1 my-[5px] h-px bg-line" role="separator"></div>
		{#if treeActions.entries}
			<button type="button" role="menuitem" class="{MENU_ITEM_CLASS} text-danger" onclick={() => run(close, deleteEntries)}>
				<Icon name="trash" className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
				Delete entries…
			</button>
		{/if}
		<button type="button" role="menuitem" class="{MENU_ITEM_CLASS} text-danger" onclick={() => run(close, () => void remove())}>
			<Icon name="trash" className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
			Delete collection…
		</button>
	{/snippet}
</Menu>

{#if nameMode}
	<CollectionNameDialog
		title={nameMode === 'rename' ? 'Rename collection' : 'New subcollection'}
		description={nameMode === 'rename' ? `Rename “${item.name}”.` : `Create in “${item.name}”.`}
		initial={nameMode === 'rename' ? item.name : ''}
		onSubmit={submitName}
		onClose={() => (nameMode = null)}
	/>
{/if}

{#if moving}
	<CollectionMoveDialog
		title="Move “{item.name}”"
		{collections}
		blockedIds={moveBlocked}
		showTopLevel={(item.parent_id ?? null) !== null}
		onMove={(targetId) => treeActions.onMove(item.id, targetId)}
		onClose={() => (moving = false)}
	/>
{/if}
