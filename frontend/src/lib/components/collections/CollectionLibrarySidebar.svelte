<script lang="ts" generics="T extends CollectionLike">
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton } from '$lib/components/ui';
	import { toasts } from '$lib/stores/toast';
	import {
		Pane,
		PaneTree,
		PaneRow,
		PaneSectionLabel,
		ExpansionState,
		buildTree,
		flattenTree,
		descendantIds,
		type RowContext
	} from '$lib/components/pane';
	import type { CollectionLike, MutationResult, SmartView, TreeActions } from './types';
	import { dropRedundantDescendants, blockedBulkMoveTargets } from './bulkMove';
	import CollectionSelectionActionBar from './CollectionSelectionActionBar.svelte';

	let {
		storageKey,
		collections,
		activeId,
		smartViews,
		treeActions,
		onCreateRoot,
		onCollapse
	}: {
		storageKey: string;
		collections: T[];
		activeId: string | undefined;
		smartViews: SmartView[];
		treeActions: TreeActions;
		onCreateRoot: (name: string) => Promise<MutationResult>;
		onCollapse: () => void;
	} = $props();

	// Expand/collapse state, persisted per caller so the two trees (history vs
	// models) don't clobber each other's saved shape.
	const expansion = new ExpansionState(storageKey);

	let tree = $derived(buildTree(collections));

	// Multi-select. Checkboxes only render while selectionMode is on, toggled
	// from the pane header - unlike the history/model grids, folder rows are
	// too dense for an always-visible checkbox column.
	let selectionMode = $state(false);
	let selectedIds = $state(new Set<string>());

	// A folder selected alongside one of its own descendants moves the
	// descendant along for free (its whole subtree relocates with it), so the
	// descendant is dropped from the batch actually sent to the API - and from
	// the count shown in the action bar, so the user sees what's really moving.
	let effectiveSelectedIds = $derived(dropRedundantDescendants(selectedIds, collections));
	let bulkMoveBlocked = $derived(blockedBulkMoveTargets(effectiveSelectedIds, collections));

	function toggleSelectionMode() {
		selectionMode = !selectionMode;
		if (!selectionMode) selectedIds = new Set();
	}

	function toggleSelect(id: string, checked: boolean) {
		const next = new Set(selectedIds);
		if (checked) next.add(id);
		else next.delete(id);
		selectedIds = next;
	}

	// In selection mode the row itself is the checkbox's hit target too - only
	// the chevron (own stopPropagation) and the kebab menu escape this.
	function handleRowClick(id: string) {
		if (selectionMode) {
			toggleSelect(id, !selectedIds.has(id));
		} else {
			treeActions.onSelect(id);
		}
	}

	// Clears the batch without leaving selection mode - the header toggle is
	// the only thing that hides the checkbox column, so a "Clear" click just
	// starts a fresh batch rather than forcing the user to re-enter the mode.
	function clearSelection() {
		selectedIds = new Set();
	}

	async function handleBulkMove(targetId: string | null) {
		const ids = effectiveSelectedIds;
		const result = await treeActions.onBulkMove(ids, targetId);
		if (!result.success) {
			toasts.error(result.message ?? result.error ?? 'Failed to move folders.');
		} else if (result.failed > 0) {
			const byId = new Map(collections.map((c) => [c.id, c.name]));
			const names = result.errors.map((e) => byId.get(e.id) ?? e.id);
			toasts.error(`Moved ${result.moved} of ${ids.length}. Failed: ${names.join(', ')}`);
		} else if (result.moved > 0) {
			toasts.success(`Moved ${result.moved} folder${result.moved === 1 ? '' : 's'}.`);
		}
		clearSelection();
	}

	// New root-level folder
	let creatingRoot = $state(false);
	let rootName = $state('');
	let rootBusy = $state(false);

	function startCreateRoot() {
		rootName = '';
		creatingRoot = true;
	}

	async function commitCreateRoot() {
		// Close synchronously so a second trigger (Enter fires blur via the
		// disabled input) bails out instead of creating a duplicate.
		if (!creatingRoot || rootBusy) return;
		creatingRoot = false;
		const name = rootName.trim();
		rootName = '';
		if (!name) return;
		try {
			rootBusy = true;
			await onCreateRoot(name);
		} finally {
			rootBusy = false;
		}
	}

	// Per-node inline editing / kebab menu state, id-keyed (only one node can
	// be renaming, creating a child, or have its menu open at a time — mirrors
	// the old per-node component instance state).
	let renamingId = $state<string | null>(null);
	let renameValue = $state('');
	let renameBusy = $state(false);

	let creatingChildId = $state<string | null>(null);
	let childName = $state('');
	let createChildBusy = $state(false);

	let menuOpenId = $state<string | null>(null);
	let showMoveMenu = $state(false);
	let confirmingDelete = $state(false);
	let menuBusy = $state(false);
	let menuError = $state<string | null>(null);

	// Valid "Move to…" targets: every folder at any depth EXCEPT the open node
	// and its descendants (those would form a cycle).
	let blocked = $derived(menuOpenId ? descendantIds(collections, menuOpenId) : new Set<string>());
	let moveTargets = $derived(
		flattenTree(buildTree(collections)).filter((n) => !blocked.has(n.item.id))
	);

	function closeMenu() {
		menuOpenId = null;
		showMoveMenu = false;
		confirmingDelete = false;
		menuError = null;
	}

	function toggleMenu(e: MouseEvent, id: string) {
		e.stopPropagation();
		if (menuOpenId === id) {
			closeMenu();
		} else {
			menuOpenId = id;
			showMoveMenu = false;
			confirmingDelete = false;
			menuError = null;
		}
	}

	function startRename(item: T) {
		renameValue = item.name;
		renamingId = item.id;
		closeMenu();
	}

	async function commitRename(item: T) {
		if (renamingId !== item.id || renameBusy) return;
		renamingId = null;
		const name = renameValue.trim();
		if (!name || name === item.name) return;
		try {
			renameBusy = true;
			await treeActions.onRename(item.id, name);
		} catch (e) {
			logger.error('Rename failed:', getErrorMessage(e));
		} finally {
			renameBusy = false;
		}
	}

	function startCreateChild(item: T) {
		childName = '';
		creatingChildId = item.id;
		closeMenu();
		if (!expansion.has(item.id)) expansion.expand(item.id);
	}

	async function commitCreateChild(item: T) {
		// Close synchronously so a second trigger (Enter fires blur via the
		// disabled input) bails out instead of creating a duplicate.
		if (creatingChildId !== item.id || createChildBusy) return;
		creatingChildId = null;
		const name = childName.trim();
		childName = '';
		if (!name) return;
		try {
			createChildBusy = true;
			await treeActions.onCreate(name, item.id);
			if (!expansion.has(item.id)) expansion.expand(item.id);
		} catch (e) {
			logger.error('Create subfolder failed:', getErrorMessage(e));
		} finally {
			createChildBusy = false;
		}
	}

	async function handleDelete(item: T, blockedIds: Set<string>) {
		try {
			menuBusy = true;
			menuError = null;
			const response = await treeActions.onDelete(item.id, blockedIds);
			if (response.success) {
				closeMenu();
			} else {
				menuError = 'Failed to delete.';
			}
		} catch (e) {
			logger.error('Delete failed:', getErrorMessage(e));
			menuError = 'Failed to delete.';
		} finally {
			menuBusy = false;
		}
	}

	async function handleMove(item: T, targetId: string | null) {
		try {
			menuBusy = true;
			menuError = null;
			const response = await treeActions.onMove(item.id, targetId);
			if (response.success) {
				closeMenu();
			} else {
				menuError = response.error ?? response.message ?? 'Move failed.';
			}
		} catch (e) {
			logger.error('Move failed:', getErrorMessage(e));
			menuError = 'Move failed (would create a cycle?).';
		} finally {
			menuBusy = false;
		}
	}
</script>

<svelte:window onclick={() => menuOpenId !== null && closeMenu()} />

<Pane label="Library" {onCollapse} bodyPadding="sm">
	{#snippet children()}
		<div class="space-y-0.5">
			{#each smartViews as view (view.id)}
				<PaneRow
					size="sm"
					icon={view.icon}
					title={view.label}
					selected={view.active}
					onclick={view.onSelect}
				/>
			{/each}
		</div>

		<div class="my-2 mx-1 h-px bg-line"></div>

		<PaneSectionLabel label="Collections">
			{#snippet actions()}
				<div class="flex items-center gap-1">
					<IconButton
						icon="list-checks"
						label={selectionMode ? 'Exit selection' : 'Select folders'}
						size="sm"
						active={selectionMode}
						onclick={toggleSelectionMode}
					/>
					<IconButton icon="plus" label="New folder" size="sm" onclick={startCreateRoot} />
				</div>
			{/snippet}
		</PaneSectionLabel>

		<div class="mt-1">
			{#if creatingRoot}
				<div class="flex items-center gap-1 py-1 pl-2">
					<!-- svelte-ignore a11y_autofocus -->
					<input
						autofocus
						bind:value={rootName}
						type="text"
						placeholder="Folder name…"
						disabled={rootBusy}
						class="flex-1 min-w-0 px-1.5 py-0.5 text-xs bg-surface-2 border border-line-strong text-fg placeholder-fg-subtle rounded focus:outline-none focus:ring-1 focus:ring-signal"
						onkeydown={(e) => {
							if (e.key === 'Enter') commitCreateRoot();
							if (e.key === 'Escape') creatingRoot = false;
						}}
						onblur={commitCreateRoot}
					/>
				</div>
			{/if}

			{#if tree.length === 0 && !creatingRoot}
				<p class="px-2 py-2 text-xs text-fg-subtle">No folders yet.</p>
			{:else}
				<PaneTree nodes={tree} expanded={expansion} onToggle={(id) => expansion.toggle(id)}>
					{#snippet row({ item, depth, hasChildren, expanded, toggle }: RowContext<T>)}
						{#if renamingId === item.id}
							<div class="flex items-center h-7" style="padding-left: {depth * 12 + 2}px">
								<span class="flex-shrink-0 w-5 h-5" aria-hidden="true"></span>
								<!-- svelte-ignore a11y_autofocus -->
								<input
									autofocus
									bind:value={renameValue}
									type="text"
									disabled={renameBusy}
									class="flex-1 min-w-0 mr-1 px-1.5 py-0.5 text-xs bg-surface-2 border border-line-strong text-fg rounded focus:outline-none focus:ring-1 focus:ring-signal"
									onclick={(e) => e.stopPropagation()}
									onkeydown={(e) => {
										if (e.key === 'Enter') commitRename(item);
										if (e.key === 'Escape') renamingId = null;
									}}
									onblur={() => commitRename(item)}
								/>
							</div>
						{:else}
							<PaneRow
								size="sm"
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
								onclick={() => handleRowClick(item.id)}
							>
								{#snippet actions()}
									<button
										type="button"
										class="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded text-fg-subtle hover:text-fg hover:bg-surface-3/60"
										onclick={(e) => toggleMenu(e, item.id)}
										aria-label="Folder actions"
										aria-haspopup="menu"
										aria-expanded={menuOpenId === item.id}
									>
										<Icon name="more" className="w-3.5 h-3.5" />
									</button>
									{#if menuOpenId === item.id}
										<div
											class="absolute right-1 top-full mt-0.5 z-50 w-52 bg-surface-1 border border-line-strong rounded-lg shadow-floating py-1"
											role="menu"
											tabindex="-1"
											onclick={(e) => e.stopPropagation()}
											onkeydown={(e) => e.stopPropagation()}
										>
											{#if showMoveMenu}
												<div class="px-2 py-1 text-2xs uppercase tracking-wide text-fg-subtle">
													Move to…
												</div>
												<div class="max-h-56 overflow-y-auto">
													<button
														class="w-full text-left px-2 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-1.5 disabled:opacity-50"
														disabled={menuBusy || (item.parent_id ?? null) === null}
														onclick={() => handleMove(item, null)}
														role="menuitem"
													>
														<span class="text-fg-subtle">／</span> Top level
													</button>
													{#each moveTargets as target (target.item.id)}
														<button
															class="w-full text-left pr-2 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-1.5 disabled:opacity-50"
															style="padding-left: {target.depth * 12 + 8}px"
															disabled={menuBusy || (item.parent_id ?? null) === target.item.id}
															onclick={() => handleMove(item, target.item.id)}
															role="menuitem"
														>
															<Icon name="folder" className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" />
															<span class="truncate">{target.item.name}</span>
														</button>
													{/each}
												</div>
												{#if menuError}
													<p class="px-2 pt-1 text-2xs text-danger">{menuError}</p>
												{/if}
												<div class="my-1 h-px bg-line"></div>
												<button
													class="w-full text-left px-2 py-1.5 text-xs text-fg-subtle hover:text-fg hover:bg-surface-2"
													onclick={() => (showMoveMenu = false)}
													role="menuitem"
												>
													Back
												</button>
											{:else if confirmingDelete}
												<div class="px-2 py-1.5 text-xs text-fg-muted">
													Delete "{item.name}"?
													{#if hasChildren}
														<span class="block text-2xs text-warning mt-0.5"
															>This also deletes its subfolders.</span
														>
													{/if}
												</div>
												{#if menuError}
													<p class="px-2 text-2xs text-danger">{menuError}</p>
												{/if}
												<div class="flex items-center gap-1 px-2 pt-1">
													<button
														class="px-2 py-1 text-xs bg-danger-solid text-white rounded hover:bg-danger-solid/90 disabled:opacity-50"
														disabled={menuBusy}
														onclick={() => handleDelete(item, blocked)}
														role="menuitem"
													>
														Delete
													</button>
													<button
														class="px-2 py-1 text-xs text-fg-muted hover:text-fg"
														onclick={() => (confirmingDelete = false)}
														role="menuitem"
													>
														Cancel
													</button>
												</div>
											{:else}
												<button
													class="w-full text-left px-2 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-2"
													onclick={() => startCreateChild(item)}
													role="menuitem"
												>
													<Icon name="folder-plus" className="w-3.5 h-3.5" /> New subfolder
												</button>
												<button
													class="w-full text-left px-2 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-2"
													onclick={() => startRename(item)}
													role="menuitem"
												>
													<Icon name="pencil" className="w-3.5 h-3.5" /> Rename
												</button>
												<button
													class="w-full text-left px-2 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-2"
													onclick={() => {
														showMoveMenu = true;
														menuError = null;
													}}
													role="menuitem"
												>
													<Icon name="arrow-right" className="w-3.5 h-3.5" /> Move to…
												</button>
												<div class="my-1 h-px bg-line"></div>
												<button
													class="w-full text-left px-2 py-1.5 text-xs text-danger hover:bg-danger/10 flex items-center gap-2"
													onclick={() => (confirmingDelete = true)}
													role="menuitem"
												>
													<Icon name="trash" className="w-3.5 h-3.5" /> Delete
												</button>
											{/if}
										</div>
									{/if}
								{/snippet}
							</PaneRow>
						{/if}

						{#if creatingChildId === item.id}
							<div class="flex items-center gap-1 py-1" style="padding-left: {(depth + 1) * 12 + 22}px">
								<!-- svelte-ignore a11y_autofocus -->
								<input
									autofocus
									bind:value={childName}
									type="text"
									placeholder="Subfolder name…"
									disabled={createChildBusy}
									class="flex-1 min-w-0 px-1.5 py-0.5 text-xs bg-surface-2 border border-line-strong text-fg placeholder-fg-subtle rounded focus:outline-none focus:ring-1 focus:ring-signal"
									onkeydown={(e) => {
										if (e.key === 'Enter') commitCreateChild(item);
										if (e.key === 'Escape') creatingChildId = null;
									}}
									onblur={() => commitCreateChild(item)}
								/>
							</div>
						{/if}
					{/snippet}
				</PaneTree>
			{/if}
		</div>
	{/snippet}
</Pane>

{#if effectiveSelectedIds.length > 0}
	<CollectionSelectionActionBar
		selectedCount={effectiveSelectedIds.length}
		{collections}
		blockedTargetIds={bulkMoveBlocked}
		onClear={clearSelection}
		onMove={handleBulkMove}
	/>
{/if}
