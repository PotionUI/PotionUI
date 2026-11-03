<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import dismiss, { type DismissReason } from '$lib/actions/dismiss';
	import { tabsStore } from '$lib/stores/tabs';
	import { api } from '$lib/services/api';
	import type { Workspace, WorkspaceData } from '$lib/types/generation';
	import { toasts } from '$lib/stores/toast';
	import BookTab from '$lib/components/BookTab.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { get } from 'svelte/store';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import NewWorkspaceModal from '$lib/components/modals/NewWorkspaceModal.svelte';
	import { Input } from '$lib/components/ui';
	import { workspaceHasUnsavedChanges, hasUnsavedWorkOutsideTab } from '$lib/utils/newWorkspace';
	import { requestTabSave } from '$lib/stores/workspaceSaveRequest';
	import { queryTabDirty } from '$lib/stores/workspaceDirtyQuery';
	import CloseTabModal from '$lib/tabs/CloseTabModal.svelte';
	import { requestCloseTab } from '$lib/tabs/closeConfirm';
	import { tabSessionDirty } from '$lib/stores/tabSessionDirty';
	import { sessionLiveSync, remoteChangeLabel } from '$lib/stores/sessionLiveSync';
	import { tabShowsUnsavedChanges } from '$lib/tabs/tabDirty';
	import { isToolTab, type Tab, type ToolTab } from '$lib/types/tabs';
	import type { WorkspaceTab } from '$lib/types/generation';
	import NewTabMenu from '$lib/tool-tabs/NewTabMenu.svelte';
	import { toolKinds } from '$lib/tool-tabs/registry';

	$: tabs = $tabsStore.tabs;
	$: activeTabId = $tabsStore.activeTabId;

	const liveSyncMarks = sessionLiveSync.marks;

	let showWorkspaceMenu = false;
	let workspaces: Workspace[] = [];
	let showSaveWorkspaceModal = false;
	let saveWorkspaceDialog: ReturnType<typeof ConfirmDialog>;
	let workspaceName = '';
	let savingWorkspace = false;
	let workspaceModalMode: 'create' | 'update' = 'create';
	let workspaceToUpdate: Workspace | null = null;
	let activeWorkspaceId: string | null = null;

	async function loadWorkspaces() {
		try {
			const response = await api.getWorkspaces();
			if (response.success && response.data) {
				workspaces = response.data;
			}
		} catch (e) {
			console.error('Failed to load workspaces:', e);
		}
	}

	function workspaceTabFor(tab: Tab): WorkspaceTab {
		if (isToolTab(tab)) {
			return {
				kind: 'tool',
				name: tab.name,
				color: tab.color || null,
				tool: tab.tool,
				source: tab.source,
				state: tab.state ?? null,
				activeTool: tab.activeTool ?? null,
				renamed: tab.renamed ?? false
			};
		}
		return {
			name: tab.name,
			color: tab.color || null,
			preset_id: tab.selectedPreset,
			mode: tab.selectedMode,
			autoTagIds: tab.autoTagIds || [],
			autoCollectionIds: tab.autoCollectionIds || []
		};
	}

	function getCurrentWorkspaceData(): WorkspaceData {
		return { tabs: tabs.map(workspaceTabFor) };
	}

	function openCreateWorkspaceModal() {
		workspaceModalMode = 'create';
		workspaceToUpdate = null;
		workspaceName = '';
		showSaveWorkspaceModal = true;
		showWorkspaceMenu = false;
	}

	function openUpdateWorkspaceModal(workspace: Workspace) {
		workspaceModalMode = 'update';
		workspaceToUpdate = workspace;
		workspaceName = workspace.name;
		showSaveWorkspaceModal = true;
		showWorkspaceMenu = false;
	}

	async function saveCurrentWorkspace() {
		if (!workspaceName.trim()) return false;
		savingWorkspace = true;
		try {
			const workspaceData = getCurrentWorkspaceData();
			if (workspaceModalMode === 'update' && workspaceToUpdate) {
				const response = await api.updateWorkspace(workspaceToUpdate.id, {
					name: workspaceName.trim(),
					data: workspaceData
				});
				if (!response.success) throw new Error(response.error || 'Failed to update workspace');
				activeWorkspaceId = workspaceToUpdate.id;
				toasts.success(`Updated “${workspaceName.trim()}”`);
			} else {
				const response = await api.saveWorkspace({ name: workspaceName.trim(), data: workspaceData });
				if (!response.success) throw new Error(response.error || 'Failed to save workspace');
				activeWorkspaceId = response.data?.id ?? null;
				toasts.success(`Saved “${workspaceName.trim()}”`);
			}
			showSaveWorkspaceModal = false;
			workspaceName = '';
			workspaceToUpdate = null;
			await loadWorkspaces();
			return true;
		} catch (e) {
			console.error('Failed to save workspace:', e);
			toasts.error(e instanceof Error ? e.message : 'Failed to save workspace');
			return false;
		} finally {
			savingWorkspace = false;
		}
	}

	async function loadWorkspace(workspace: Workspace) {
		const data = workspace.data;
		if (!data?.tabs?.length) return;

		tabsStore.reset();
		const placeholderId = get(tabsStore).tabs[0].id;
		for (const wsTab of data.tabs) {
			if (wsTab.kind === 'tool' && wsTab.tool) {
				tabsStore.addToolTabWithData({
					tool: wsTab.tool,
					name: wsTab.name,
					color: wsTab.color || null,
					source: wsTab.source ?? null,
					state: wsTab.state ?? null,
					activeTool: wsTab.activeTool ?? null,
					renamed: wsTab.renamed ?? false
				});
			} else {
				tabsStore.addTabWithData(wsTab.name, {
					color: wsTab.color || null,
					selectedPreset: wsTab.preset_id ?? null,
					selectedMode: wsTab.mode ?? null,
					autoTagIds: wsTab.autoTagIds || [],
					autoCollectionIds: wsTab.autoCollectionIds || []
				});
			}
		}
		tabsStore.removeTab(placeholderId);
		activeWorkspaceId = workspace.id;
		showWorkspaceMenu = false;
	}

	async function deleteWorkspace(workspaceId: string) {
		try {
			const response = await api.deleteWorkspace(workspaceId);
			if (!response.success) throw new Error(response.error || 'Failed to delete workspace');
			if (activeWorkspaceId === workspaceId) activeWorkspaceId = null;
			await loadWorkspaces();
		} catch (e) {
			console.error('Failed to delete workspace:', e);
			toasts.error(e instanceof Error ? e.message : 'Failed to delete workspace');
		}
	}

	function handleWorkspaceDismiss(reason: DismissReason, event: Event) {
		if (reason === 'outside' && event.target instanceof Element && event.target.closest('.workspace-menu-container')) return;
		showWorkspaceMenu = false;
	}

	function toggleWorkspaceMenu() {
		showWorkspaceMenu = !showWorkspaceMenu;
		if (showWorkspaceMenu) void loadWorkspaces();
	}

	function addTab() {
		tabsStore.addTab();
	}

	function addToolTab(tool: string) {
		tabsStore.openToolTab(tool, null);
	}

	function toolIconFor(tab: ToolTab): string {
		return $toolKinds.find((kind) => kind.tool === tab.tool)?.icon ?? 'extension';
	}

	function toolLabelFor(tab: ToolTab): string {
		return $toolKinds.find((kind) => kind.tool === tab.tool)?.label ?? 'Tool';
	}

	function unsavedEditsLabel(tab: ToolTab): string {
		const edits = tab.dirty ?? 0;
		return `${edits} unsaved ${edits === 1 ? 'edit' : 'edits'}`;
	}

	function removeTab(tabId: string) {
		requestCloseTab(tabId);
	}

	function setActiveTab(tabId: string) {
		tabsStore.setActiveTab(tabId);
	}

	function handleTabRename(event: CustomEvent<{ id: string; name: string }>) {
		tabsStore.renameTab(event.detail.id, event.detail.name);
	}

	function handleTabReorder(event: CustomEvent<{ draggedId: string; targetId: string; position: 'left' | 'right' }>) {
		tabsStore.reorderTabs(event.detail.draggedId, event.detail.targetId, event.detail.position);
	}

	// "New workspace": wipe every open tab down to one fresh empty tab. Reuses
	// tabsStore.reset() — the same call the "Load workspace" flow above already
	// uses — so a fresh tab comes out of the one code path already proven to
	// reset per-tab state cleanly (new id, createDefaultTab()), and its `set()`
	// call naturally supersedes the debounced localStorage save (see
	// stores/tabs.ts) rather than racing it.
	let showNewWorkspaceModal = false;
	let newWorkspaceSaving = false;
	let newWorkspaceOtherTabsDirty = false;
	let checkingNewWorkspace = false;

	async function handleNewWorkspaceClick() {
		if (checkingNewWorkspace) return;
		checkingNewWorkspace = true;
		try {
			const tabsSnapshot = tabs;
			const activeTabIdSnapshot = activeTabId;
			const activeIsTool = isToolTab(tabsSnapshot.find((tab) => tab.id === activeTabIdSnapshot));
			const liveActiveDirty = activeIsTool ? false : await queryTabDirty(activeTabIdSnapshot);
			const dirty = workspaceHasUnsavedChanges(tabsSnapshot) || liveActiveDirty === true;
			if (!dirty) {
				tabsStore.reset();
				return;
			}
			newWorkspaceOtherTabsDirty = hasUnsavedWorkOutsideTab(tabsSnapshot, activeTabIdSnapshot);
			showNewWorkspaceModal = true;
		} finally {
			checkingNewWorkspace = false;
		}
	}

	function cancelNewWorkspace() {
		if (newWorkspaceSaving) return;
		showNewWorkspaceModal = false;
	}

	function discardAndCreateNewWorkspace() {
		showNewWorkspaceModal = false;
		tabsStore.reset();
	}

	async function saveAndCreateNewWorkspace() {
		if (isToolTab(tabs.find((tab) => tab.id === activeTabId))) {
			toasts.error('Switch to a generation tab to save its session first.');
			return;
		}
		newWorkspaceSaving = true;
		try {
			const ok = await requestTabSave(activeTabId);
			if (ok) {
				showNewWorkspaceModal = false;
				tabsStore.reset();
			} else {
				toasts.error('Failed to save the session — nothing was discarded.');
			}
		} finally {
			newWorkspaceSaving = false;
		}
	}
</script>


<CloseTabModal />

<!-- Generation Tab Bar at Top (Desktop) -->
<div class="tab-bar-container flex-shrink-0 hidden md:block">
	<div class="px-4 h-full">
		<div class="flex items-center h-full gap-4">
			<!-- Left cluster: tabs + add + workspace menu, bottom-aligned within itself -->
			<div class="flex min-w-0 flex-1 items-end self-stretch gap-0 overflow-x-auto no-scrollbar">
				{#each tabs as tab (tab.id)}
					{#if isToolTab(tab)}
						<BookTab
							id={tab.id}
							name={tab.name}
							color={tab.color || null}
							isActive={tab.id === activeTabId}
							canDelete={tabs.length > 1}
							toolIcon={toolIconFor(tab)}
							toolLabel={toolLabelFor(tab)}
							thumbnail={tab.thumbnail ?? null}
							progress={tab.run ? (tab.run.progress ?? 0) : null}
							unsaved={(tab.dirty ?? 0) > 0}
							unsavedLabel={unsavedEditsLabel(tab)}
							on:select={(e) => setActiveTab(e.detail.id)}
							on:rename={handleTabRename}
							on:delete={(e) => removeTab(e.detail.id)}
							on:drop={handleTabReorder}
							on:colorChange={(e) => tabsStore.updateTab(e.detail.id, { color: e.detail.color })}
						/>
					{:else}
						<BookTab
							id={tab.id}
							name={tab.name}
							color={tab.color || null}
							isActive={tab.id === activeTabId}
							canDelete={tabs.length > 1}
							isGenerating={tab.generation.isGenerating}
							unsaved={tabShowsUnsavedChanges(tab, $tabSessionDirty)}
							remoteChange={remoteChangeLabel($liveSyncMarks[tab.id])}
							on:select={(e) => setActiveTab(e.detail.id)}
							on:rename={handleTabRename}
							on:delete={(e) => removeTab(e.detail.id)}
							on:drop={handleTabReorder}
							on:colorChange={(e) => tabsStore.updateTab(e.detail.id, { color: e.detail.color })}
						/>
					{/if}
				{/each}

				<NewTabMenu triggerClass="new-tab-button" onNewGeneration={addTab} onOpenTool={addToolTab} />

				<!-- New Workspace Button -->
				<Tooltip text="New workspace" position="bottom" delay={150}>
					<button
						class="new-tab-button"
						on:click={handleNewWorkspaceClick}
						type="button"
						aria-label="New workspace"
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
						</svg>
					</button>
				</Tooltip>

			</div>

			<!-- Workspace menu sits OUTSIDE the scrollable tab cluster: its
			     overflow-x-auto clips the dropdown (overflow on one axis forces
			     clipping on the other), which made the menu open invisibly. -->
			<div class="workspace-menu-container relative">
					<button
						class="new-tab-button"
						on:click|stopPropagation={toggleWorkspaceMenu}
						type="button"
						use:tooltip={'Workspaces'}
						aria-label="Workspaces"
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
						</svg>
					</button>

					{#if showWorkspaceMenu}
						<!-- Anchored right: the trigger now sits at the right edge of the
						     tab bar (see comment above), so a left-0 dropdown ran off
						     the viewport edge. Opening leftward keeps it on screen. -->
						<div use:dismiss={{ onDismiss: handleWorkspaceDismiss }} class="absolute right-0 top-full mt-1 z-50 bg-surface-1 border border-line-strong rounded-lg shadow-floating min-w-[260px] py-1">
							<div class="px-3 py-2 text-xs font-semibold text-fg-muted uppercase tracking-wider border-b border-line">Workspaces</div>

							<!-- Save current -->
							<button
								class="w-full px-3 py-2 text-sm text-left text-fg-muted hover:bg-surface-2 flex items-center gap-2"
								on:click|stopPropagation={openCreateWorkspaceModal}
							>
								<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
								</svg>
								Save current layout as new
							</button>

							{#if workspaces.length > 0}
								<div class="border-t border-line my-1"></div>
								{#each workspaces as ws}
									<div class="flex items-center group {activeWorkspaceId === ws.id ? 'bg-signal/5' : ''}">
										<button
											class="flex-1 min-w-0 px-3 py-2 text-sm text-left text-fg-muted hover:bg-surface-2 flex items-center gap-2"
											on:click|stopPropagation={() => loadWorkspace(ws)}
										>
											<span class="w-1.5 h-1.5 rounded-full flex-shrink-0 {activeWorkspaceId === ws.id ? 'bg-signal-solid' : 'bg-transparent'}"></span>
											<span class="truncate">{ws.name}</span>
										</button>
										<Tooltip text="Update with current layout" position="left" delay={150}>
										<button
											class="px-2 py-2 hover:text-signal opacity-60 hover:opacity-100 focus:opacity-100 transition-opacity {activeWorkspaceId === ws.id ? 'text-signal' : 'text-fg-subtle'}"
											on:click|stopPropagation={() => openUpdateWorkspaceModal(ws)}
											aria-label="Update {ws.name} with current layout"
										>
											<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v6h6M20 20v-6h-6M5.5 15a7 7 0 0011.9 2.4L20 14M4 10l2.6-3.4A7 7 0 0118.5 9" />
											</svg>
										</button>
										</Tooltip>
										<Tooltip text="Delete workspace" position="left" delay={150}>
										<button
											class="px-2 py-2 text-fg-subtle hover:text-danger opacity-60 hover:opacity-100 focus:opacity-100 transition-opacity"
											on:click|stopPropagation={() => deleteWorkspace(ws.id)}
											aria-label="Delete {ws.name}"
										>
											<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
											</svg>
										</button>
										</Tooltip>
									</div>
								{/each}
							{:else}
								<div class="px-3 py-2 text-xs text-fg-subtle italic">No saved workspaces</div>
							{/if}
						</div>
					{/if}
				</div>
		</div>
	</div>
</div>

<!-- Save Workspace Modal -->
<ConfirmDialog
	bind:this={saveWorkspaceDialog}
	isOpen={showSaveWorkspaceModal}
	title={workspaceModalMode === 'update' ? 'Update Workspace' : 'Save Workspace'}
	size="sm"
	busy={savingWorkspace}
	confirmLabel={workspaceModalMode === 'update' ? 'Update workspace' : 'Save as new'}
	confirmDisabled={!workspaceName.trim()}
	onConfirm={saveCurrentWorkspace}
	onCancel={() => (showSaveWorkspaceModal = false)}
>
	<div class="p-6">
		<p class="text-sm text-fg-muted mb-4">
			{workspaceModalMode === 'update'
				? 'Replace this workspace with the current tab layout. You can also rename it.'
				: 'Save the current tab layout (names, colors, presets, modes, editor tabs).'}
		</p>
		<Input
			type="text"
			class="mb-4"
			placeholder="Workspace name..."
			bind:value={workspaceName}
			onkeydown={(e: KeyboardEvent) => e.key === 'Enter' && saveWorkspaceDialog.confirm()}
			data-autofocus
		/>
	</div>
</ConfirmDialog>

<NewWorkspaceModal
	isOpen={showNewWorkspaceModal}
	otherTabsAlsoDirty={newWorkspaceOtherTabsDirty}
	busy={newWorkspaceSaving}
	on:save={saveAndCreateNewWorkspace}
	on:discard={discardAndCreateNewWorkspace}
	on:cancel={cancelNewWorkspace}
/>

<style>
	.tab-bar-container {
		background-color: rgb(var(--canvas));
		background-image: radial-gradient(rgb(var(--fg) / 0.045) 0.75px, transparent 0.75px);
		background-position: 2px 2px;
		background-size: 14px 14px;
		border-bottom: 1px solid rgb(var(--line));
		height: var(--header-h);
	}

	.no-scrollbar {
		-ms-overflow-style: none;
		scrollbar-width: none;
	}

	.no-scrollbar::-webkit-scrollbar {
		display: none;
	}

	.tab-bar-container :global(.new-tab-button) {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		margin-bottom: 0.25rem;
		padding: 0.375rem;
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
		border-radius: 0.25rem;
		color: rgb(var(--fg-subtle));
		cursor: pointer;
		transition: all 0.2s ease;
		flex-shrink: 0;
		margin-left: 0.5rem;
	}

	.tab-bar-container :global(.new-tab-button:hover) {
		color: rgb(var(--fg-muted));
		background: rgb(var(--surface-3));
		border-color: rgb(var(--line-strong));
		box-shadow: 0 1px 2px rgb(0 0 0 / 0.3);
	}

	.tab-bar-container :global(.new-tab-button:active) {
		transform: scale(0.95);
	}
</style>
