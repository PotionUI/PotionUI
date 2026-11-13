<script lang="ts" generics="T extends CollectionLike">
	import { getContext, onMount, untrack } from 'svelte';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, IconButton, Input, Menu } from '$lib/components/ui';
	import { autoOrganizeCounts, autoOrganizeHref, type AutoOrganizeSubject } from '$lib/stores/autoOrganizeCounts';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import {
		Pane,
		PaneTree,
		PaneRow,
		ExpansionState,
		buildTree,
		flattenTree,
		filterTree,
		descendantIds,
		type RowContext
	} from '$lib/components/pane';
	import type { BulkMoveResult, CollectionLike, MutationResult, SmartView, TreeActions } from './types';
	import { dropRedundantDescendants, blockedBulkMoveTargets } from './bulkMove';
	import CollectionActionsMenu from './CollectionActionsMenu.svelte';
	import CollectionNameDialog from './CollectionNameDialog.svelte';
	import CollectionMoveDialog from './CollectionMoveDialog.svelte';
	import { COLLECTION_SHEET, MENU_ITEM_CLASS, type CollectionSheet } from './sidebar';
	import type { OrganizeCollectionScope } from '$lib/types/organize';

	let {
		storageKey,
		label,
		collections,
		activeId,
		allView,
		favoritesView,
		unsortedView,
		kitsView,
		autoOrganizeSubject,
		treeActions,
		onCreateRoot,
		onCollapse,
		embedded = false,
		rulesScope
	}: {
		storageKey: string;
		label: string;
		collections: T[];
		activeId: string | undefined;
		allView: SmartView;
		favoritesView?: SmartView;
		unsortedView: SmartView;
		kitsView?: SmartView;
		autoOrganizeSubject?: AutoOrganizeSubject;
		treeActions: TreeActions;
		onCreateRoot: (name: string) => Promise<MutationResult>;
		onCollapse?: () => void;
		embedded?: boolean;
		rulesScope?: OrganizeCollectionScope;
	} = $props();

	const ROOT_DROP = '__all__';
	const sheet = getContext<CollectionSheet | undefined>(COLLECTION_SHEET);
	const expansion = new ExpansionState(untrack(() => storageKey));

	let autoCount = $derived(
		autoOrganizeSubject ? ($autoOrganizeCounts[autoOrganizeSubject] ?? null) : null
	);

	onMount(() => {
		if (autoOrganizeSubject) void autoOrganizeCounts.load();
	});

	let query = $state('');
	let needle = $derived(query.trim().toLowerCase());
	let tree = $derived(buildTree(collections));
	let visibleTree = $derived(
		needle ? filterTree(tree, (c) => c.name.toLowerCase().includes(needle)) : tree
	);
	let searchExpanded = $derived(new Set(flattenTree(visibleTree).map((node) => node.item.id)));

	let searchEl: HTMLDivElement | undefined = $state();
	let treeEl: HTMLDivElement | undefined = $state();

	let menuOpenId = $state<string | null>(null);
	let createName = $state<string | null>(null);
	let bulkMoving = $state(false);

	let selectionMode = $state(false);
	let selectedIds = $state(new Set<string>());
	let effectiveSelectedIds = $derived(dropRedundantDescendants(selectedIds, collections));
	let bulkMoveBlocked = $derived(blockedBulkMoveTargets(effectiveSelectedIds, collections));

	let dragId = $state<string | null>(null);
	let dropTargetId = $state<string | null>(null);

	function selectView(view: SmartView) {
		void view.onSelect();
		sheet?.close();
	}

	function handleRowClick(id: string) {
		if (selectionMode) {
			toggleSelect(id, !selectedIds.has(id));
			return;
		}
		void treeActions.onSelect(id);
		sheet?.close();
	}

	function clearSearch() {
		query = '';
		searchEl?.querySelector('input')?.focus();
	}

	function handleSearchKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && query) {
			e.preventDefault();
			e.stopPropagation();
			clearSearch();
		} else if (e.key === 'ArrowDown') {
			const first = treeEl?.querySelector<HTMLElement>('[data-pane-row]');
			if (!first) return;
			e.preventDefault();
			first.focus();
		}
	}

	function toggleTree(id: string) {
		if (!needle) expansion.toggle(id);
	}

	function toggleSelectionMode() {
		selectionMode = !selectionMode;
		selectedIds = new Set();
	}

	function toggleSelect(id: string, checked: boolean) {
		const next = new Set(selectedIds);
		if (checked) next.add(id);
		else next.delete(id);
		selectedIds = next;
	}

	function exitSelection() {
		selectionMode = false;
		selectedIds = new Set();
	}

	function canDrop(targetId: string | null): boolean {
		if (!dragId) return false;
		const dragged = collections.find((c) => c.id === dragId);
		if (!dragged) return false;
		if (targetId === null) return (dragged.parent_id ?? null) !== null;
		if ((dragged.parent_id ?? null) === targetId) return false;
		return !descendantIds(collections, dragId).has(targetId);
	}

	function handleDragStart(e: DragEvent, id: string) {
		dragId = id;
		if (e.dataTransfer) {
			e.dataTransfer.effectAllowed = 'move';
			e.dataTransfer.setData('text/plain', id);
		}
	}

	function handleDragEnd() {
		dragId = null;
		dropTargetId = null;
	}

	function handleDragOver(e: DragEvent, targetId: string | null) {
		e.stopPropagation();
		if (!canDrop(targetId)) return;
		e.preventDefault();
		dropTargetId = targetId ?? ROOT_DROP;
	}

	function handleDragLeave(targetId: string | null) {
		if (dropTargetId === (targetId ?? ROOT_DROP)) dropTargetId = null;
	}

	async function handleDrop(e: DragEvent, targetId: string | null) {
		e.preventDefault();
		e.stopPropagation();
		const id = dragId;
		const allowed = canDrop(targetId);
		handleDragEnd();
		if (!id || !allowed) return;
		try {
			const result = await treeActions.onMove(id, targetId);
			if (!result.success) toasts.error(result.message ?? result.error ?? 'Move failed.');
			else if (targetId) expansion.expand(targetId);
		} catch (err) {
			logger.error('Drag move failed:', getErrorMessage(err));
			toasts.error('Move failed.');
		}
	}

	async function createRoot(name: string): Promise<MutationResult> {
		const result = await onCreateRoot(name);
		if (result.success) query = '';
		return result;
	}

	async function moveEach(ids: string[], targetId: string | null): Promise<BulkMoveResult> {
		let moved = 0;
		const errors: { id: string; reason: string }[] = [];
		for (const id of ids) {
			try {
				const result = await treeActions.onMove(id, targetId);
				if (result.success) moved++;
				else errors.push({ id, reason: result.error ?? result.message ?? 'Move failed.' });
			} catch (err) {
				errors.push({ id, reason: getErrorMessage(err) });
			}
		}
		return { success: true, moved, failed: errors.length, errors };
	}

	async function handleBulkMove(targetId: string | null): Promise<MutationResult> {
		const ids = effectiveSelectedIds;
		const names = new Map(collections.map((c) => [c.id, c.name]));
		const result = treeActions.onBulkMove
			? await treeActions.onBulkMove(ids, targetId)
			: await moveEach(ids, targetId);
		if (!result.success) {
			toasts.error(result.message ?? result.error ?? 'Failed to move collections.');
		} else if (result.failed > 0) {
			const failed = result.errors.map((e) => names.get(e.id) ?? e.id);
			toasts.error(`Moved ${result.moved} of ${ids.length}. Failed: ${failed.join(', ')}`);
		} else if (result.moved > 0) {
			toasts.success(`Moved ${result.moved} collection${result.moved === 1 ? '' : 's'}.`);
		}
		if (targetId) expansion.expand(targetId);
		selectedIds = new Set();
		return { success: true };
	}

	async function handleBulkDelete() {
		const ids = effectiveSelectedIds;
		if (ids.length === 0) return;
		const snapshot = [...collections];
		const names = new Map(snapshot.map((c) => [c.id, c.name]));
		const quoted = ids.map((id) => `“${names.get(id) ?? id}”`).join(', ');
		const many = ids.length > 1;
		const confirmed = await confirmDialog({
			title: many ? `Delete ${ids.length} collections?` : 'Delete collection?',
			message: `${quoted} and any subcollections will be deleted. Items stay in All; only the collections and their memberships are removed.`,
			variant: 'danger',
			confirmLabel: many ? 'Delete collections' : 'Delete collection'
		});
		if (!confirmed) return;
		let deleted = 0;
		const failed: string[] = [];
		for (const id of ids) {
			try {
				const result = await treeActions.onDelete(id, descendantIds(snapshot, id));
				if (result.success) deleted++;
				else failed.push(names.get(id) ?? id);
			} catch (err) {
				logger.error('Delete failed:', getErrorMessage(err));
				failed.push(names.get(id) ?? id);
			}
		}
		if (failed.length > 0) toasts.error(`Deleted ${deleted} of ${ids.length}. Failed: ${failed.join(', ')}`);
		else toasts.success(`Deleted ${deleted} collection${deleted === 1 ? '' : 's'}.`);
		selectedIds = new Set();
	}

	function menuOpenChange(id: string, open: boolean) {
		if (open) menuOpenId = id;
		else if (menuOpenId === id) menuOpenId = null;
	}
</script>

{#snippet viewRow(view: SmartView, rootDrop: boolean)}
	<div
		role="none"
		class="rounded {rootDrop && dropTargetId === ROOT_DROP ? 'bg-signal/[0.14] ring-1 ring-inset ring-signal' : ''}"
		ondragover={(e) => rootDrop && handleDragOver(e, null)}
		ondragleave={() => rootDrop && handleDragLeave(null)}
		ondrop={(e) => rootDrop && handleDrop(e, null)}
	>
		<PaneRow
			size="nav"
			role="option"
			icon={view.icon}
			title={view.label}
			count={view.count}
			selected={view.active}
			onclick={() => selectView(view)}
		/>
	</div>
{/snippet}

{#snippet views()}
	<div class="px-3 pb-4 pt-3" role="listbox" aria-label="{label} views">
		{@render viewRow(allView, true)}
		{#if favoritesView}
			{@render viewRow(favoritesView, false)}
		{/if}
		{@render viewRow(unsortedView, false)}
		{#if kitsView}
			<div class="-mx-3 my-2.5 h-px bg-line" role="separator"></div>
			{@render viewRow(kitsView, false)}
		{/if}
	</div>
{/snippet}

{#snippet collectionsHead()}
	<div class="border-t border-line">
		<div class="flex items-center gap-0.5 pb-2 pl-5 pr-[13px] pt-3.5">
			<span class="flex-1 text-sm font-medium text-fg-muted">Collections</span>
			<IconButton icon="plus" label="New collection" size="sm" onclick={() => (createName = '')} />
			<Menu label="Collection options" width={224}>
				{#snippet children({ close })}
					<button
						type="button"
						role="menuitem"
						class="{MENU_ITEM_CLASS} text-fg"
						onclick={() => {
							close(true);
							toggleSelectionMode();
						}}
					>
						<Icon name="list-checks" className="h-4 w-4 flex-shrink-0 text-fg-muted" strokeWidth={1.75} />
						{selectionMode ? 'Exit selection' : 'Select collections'}
					</button>
					<div class="mx-1 my-[5px] h-px bg-line" role="separator"></div>
					<button
						type="button"
						role="menuitem"
						class="{MENU_ITEM_CLASS} text-fg"
						onclick={() => {
							close(true);
							expansion.expandMany(collections.map((c) => c.id));
						}}
					>
						<Icon name="chevrons-right" className="h-4 w-4 flex-shrink-0 rotate-90 text-fg-muted" strokeWidth={1.75} />
						Expand all
					</button>
					<button
						type="button"
						role="menuitem"
						class="{MENU_ITEM_CLASS} text-fg"
						onclick={() => {
							close(true);
							expansion.clear();
						}}
					>
						<Icon name="chevrons-right" className="h-4 w-4 flex-shrink-0 -rotate-90 text-fg-muted" strokeWidth={1.75} />
						Collapse all
					</button>
				{/snippet}
			</Menu>
		</div>
		<div class="relative mx-4 mb-3" bind:this={searchEl}>
			<Icon
				name="search"
				className="pointer-events-none absolute left-2.5 top-1/2 h-[15px] w-[15px] -translate-y-1/2 text-fg-muted"
			/>
			<Input
				bind:value={query}
				type="text"
				placeholder="Find a collection…"
				aria-label="Find a collection"
				autocomplete="off"
				class="pl-8 text-sm {query ? 'pr-8' : ''}"
				onkeydown={handleSearchKeydown}
			/>
			{#if query}
				<IconButton
					icon="close"
					label="Clear collection search"
					size="xs"
					class="absolute right-1.5 top-1/2 -translate-y-1/2"
					onclick={clearSearch}
				/>
			{/if}
		</div>
	</div>
{/snippet}

{#snippet treeBody()}
	<div
		bind:this={treeEl}
		role="none"
		class="min-h-full px-3 pb-4 {dropTargetId === ROOT_DROP ? 'bg-signal/5' : ''}"
		ondragover={(e) => handleDragOver(e, null)}
		ondragleave={() => handleDragLeave(null)}
		ondrop={(e) => handleDrop(e, null)}
	>
		{#if visibleTree.length === 0}
			<EmptyState compact icon="folder" title={collections.length === 0 ? 'No collections yet' : 'No matching collections'}>
				{#snippet actions()}
					<Button size="sm" variant="secondary" onclick={() => (createName = query.trim())}>
						{query.trim() ? `Create “${query.trim()}”` : 'Create collection'}
					</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<PaneTree
				nodes={visibleTree}
				rootRole="group"
				expanded={needle ? searchExpanded : expansion}
				onToggle={toggleTree}
			>
				{#snippet row({ item, depth, hasChildren, expanded, toggle }: RowContext<T>)}
					<div
						role="none"
						draggable={!selectionMode && !needle}
						class="py-px {dragId === item.id ? 'opacity-50' : ''}"
						ondragstart={(e) => handleDragStart(e, item.id)}
						ondragend={handleDragEnd}
						ondragover={(e) => handleDragOver(e, item.id)}
						ondragleave={() => handleDragLeave(item.id)}
						ondrop={(e) => handleDrop(e, item.id)}
					>
						<PaneRow
							size="nav"
							role="treeitem"
							{depth}
							expandable={hasChildren}
							{expanded}
							onToggle={toggle}
							checkable={selectionMode}
							checked={selectedIds.has(item.id)}
							onCheck={(checked) => toggleSelect(item.id, checked)}
							icon="folder"
							title={item.name}
							count={item.item_count}
							selected={activeId === item.id}
							revealActions={menuOpenId === item.id}
							class={dropTargetId === item.id ? 'bg-signal/[0.14] ring-1 ring-inset ring-signal' : ''}
							onclick={() => handleRowClick(item.id)}
						>
							{#snippet actions()}
								<CollectionActionsMenu
									{item}
									{collections}
									{treeActions}
									{rulesScope}
									onOpenChange={(open) => menuOpenChange(item.id, open)}
									onChildCreated={(id) => expansion.expand(id)}
								/>
							{/snippet}
						</PaneRow>
					</div>
				{/snippet}
			</PaneTree>
		{/if}
	</div>
{/snippet}

{#snippet selectionBar()}
	<div class="px-4 py-3 text-sm" role="group" aria-label="Collection selection">
		<div class="mb-2.5 flex items-center justify-between">
			<span class="font-mono tabular-nums text-fg-muted" data-testid="collection-selected-count"
				>{selectedIds.size} selected</span
			>
			<Button size="sm" variant="ghost" onclick={exitSelection}>Done</Button>
		</div>
		<div class="flex gap-1.5">
			<Button
				size="sm"
				variant="secondary"
				class="flex-1"
				disabled={effectiveSelectedIds.length === 0}
				onclick={() => (bulkMoving = true)}>Move…</Button
			>
			<Button
				size="sm"
				variant="secondary"
				class="flex-1 text-danger"
				disabled={effectiveSelectedIds.length === 0}
				onclick={() => void handleBulkDelete()}>Delete…</Button
			>
		</div>
	</div>
{/snippet}

{#snippet autoOrganize(subject: AutoOrganizeSubject)}
	<div class="p-3">
		<a
			href={autoOrganizeHref(subject)}
			data-testid="auto-organize-link"
			class="flex min-h-8 items-center gap-[9px] rounded px-[9px] py-[7px] text-sm text-fg-muted hover:bg-surface-2 hover:text-fg"
		>
			<Icon name="wand" className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
			<span class="flex-1 truncate">Auto-organize</span>
			{#if autoCount}
				{#if autoCount.needsAttention > 0}
					<span
						class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warning"
						role="img"
						aria-label="{autoCount.needsAttention} need attention"
					></span>
				{/if}
				<span class="flex-shrink-0 text-sm tabular-nums">{autoCount.active} active</span>
			{/if}
			<Icon name="chevron-right" className="h-[13px] w-[13px] flex-shrink-0" />
		</a>
	</div>
{/snippet}

{#snippet footerContent()}
	{#if selectionMode}
		{@render selectionBar()}
	{/if}
	{#if selectionMode && autoOrganizeSubject}
		<div class="h-px bg-line"></div>
	{/if}
	{#if autoOrganizeSubject}
		{@render autoOrganize(autoOrganizeSubject)}
	{/if}
{/snippet}

{#if embedded && !sheet}
	<div class="flex flex-col">
		{@render views()}
		{@render collectionsHead()}
		<div role="tree" aria-label="Collections">
			{@render treeBody()}
		</div>
		{#if selectionMode || autoOrganizeSubject}
			<div class="border-t border-line">
				{@render footerContent()}
			</div>
		{/if}
	</div>
{:else}
	<Pane
		{label}
		labelStyle="title"
		ariaLabel="Collections"
		bodyRole="tree"
		footer={selectionMode || autoOrganizeSubject ? footerContent : undefined}
	>
		{#snippet headerActions()}
			{#if sheet}
				<IconButton icon="close" label="Close collections" size="sm" onclick={() => sheet.close()} />
			{:else if onCollapse}
				<IconButton icon="sidebar-collapse" label="Collapse collections" size="sm" onclick={onCollapse} />
			{/if}
		{/snippet}
		{#snippet subheader()}
			{@render views()}
			{@render collectionsHead()}
		{/snippet}
		{#snippet children()}
			{@render treeBody()}
		{/snippet}
	</Pane>
{/if}

{#if createName !== null}
	<CollectionNameDialog
		title="New collection"
		description="Create in {label}."
		initial={createName}
		onSubmit={createRoot}
		onClose={() => (createName = null)}
	/>
{/if}

{#if bulkMoving}
	<CollectionMoveDialog
		title={effectiveSelectedIds.length > 1 ? `Move ${effectiveSelectedIds.length} collections` : 'Move collection'}
		{collections}
		blockedIds={bulkMoveBlocked}
		showTopLevel={true}
		onMove={handleBulkMove}
		onClose={() => (bulkMoving = false)}
	/>
{/if}
