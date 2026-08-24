<script lang="ts">
	import { tabsStore } from '$lib/stores/tabs';
	import { isMobile } from '$lib/stores/viewport';
	import { api } from '$lib/services/api';
	import type { Workspace, WorkspaceData } from '$lib/types/generation';
	import type { GenerationLayoutMode } from '$lib/stores/generationLayout';
	import { toasts } from '$lib/stores/toast';
	import BookTab from '$lib/components/BookTab.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import TabsOverflowMenu from '$lib/components/layout/TabsOverflowMenu.svelte';
	import { shortcutLabels } from '$lib/stores/keybindings';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, Input } from '$lib/components/ui';

	// Mostly self-contained: reads/writes tabsStore directly. The session
	// picker lives in the generation console bar's session/save cells, not here.

	$: tabs = $tabsStore.tabs;
	$: activeTabId = $tabsStore.activeTabId;
	$: activeTab = tabs.find((t) => t.id === activeTabId);

	function handleLayoutChange(mode: GenerationLayoutMode) {
		if (!activeTab || mode === activeTab.layoutMode) return;
		tabsStore.updateTab(activeTab.id, { layoutMode: mode });
	}

	let showWorkspaceMenu = false;
	let workspaces: Workspace[] = [];
	let showSaveWorkspaceModal = false;
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

	function getCurrentWorkspaceData(): WorkspaceData {
		return {
			tabs: tabs.map((tab) => ({
				name: tab.name,
				color: tab.color || null,
				preset_id: tab.selectedPreset,
				mode: tab.selectedMode,
				autoTagIds: tab.autoTagIds || [],
				autoCollectionIds: tab.autoCollectionIds || []
			}))
		};
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
		if (!workspaceName.trim()) return;
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
		} catch (e) {
			console.error('Failed to save workspace:', e);
			toasts.error(e instanceof Error ? e.message : 'Failed to save workspace');
		} finally {
			savingWorkspace = false;
		}
	}

	async function loadWorkspace(workspace: Workspace) {
		const data = workspace.data;
		if (!data?.tabs?.length) return;

		// Reset tabs and recreate from workspace
		tabsStore.reset();

		// Remove the default tab and create new ones from workspace
		for (let i = 0; i < data.tabs.length; i++) {
			const wsTab = data.tabs[i];
			if (i === 0) {
				// Update the first default tab
				const state = tabsStore;
				let currentState: any;
				const unsub = state.subscribe((s) => (currentState = s));
				unsub();
				const firstTabId = currentState.tabs[0].id;
				tabsStore.updateTab(firstTabId, {
					name: wsTab.name,
					color: wsTab.color || null,
					selectedPreset: wsTab.preset_id,
					selectedMode: wsTab.mode,
					autoTagIds: wsTab.autoTagIds || [],
					autoCollectionIds: wsTab.autoCollectionIds || []
				});
			} else {
				tabsStore.addTab();
				let currentState: any;
				const unsub = tabsStore.subscribe((s) => (currentState = s));
				unsub();
				const newTabId = currentState.tabs[currentState.tabs.length - 1].id;
				tabsStore.updateTab(newTabId, {
					name: wsTab.name,
					color: wsTab.color || null,
					selectedPreset: wsTab.preset_id,
					selectedMode: wsTab.mode,
					autoTagIds: wsTab.autoTagIds || [],
					autoCollectionIds: wsTab.autoCollectionIds || []
				});
			}
		}
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

	function handleWorkspaceClickOutside(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (!target.closest('.workspace-menu-container')) {
			showWorkspaceMenu = false;
		}
	}

	function toggleWorkspaceMenu() {
		showWorkspaceMenu = !showWorkspaceMenu;
		if (showWorkspaceMenu) void loadWorkspaces();
	}

	function addTab() {
		tabsStore.addTab();
	}

	function removeTab(tabId: string) {
		if (tabs.length > 1) {
			tabsStore.removeTab(tabId);
		}
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
</script>

<!-- Mobile Tab Bar -->
{#if $isMobile && tabs.length > 0}
	<div class="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-canvas border-b border-line overflow-x-auto no-scrollbar md:hidden">
		{#each tabs as tab (tab.id)}
			<div
				role="tab"
				tabindex="0"
				class="flex-shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-colors cursor-pointer
					{tab.id === activeTabId
						? 'bg-accent text-accent-contrast'
						: 'bg-surface-2 text-fg-muted hover:text-fg'}"
				on:click={() => setActiveTab(tab.id)}
				on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActiveTab(tab.id); } }}
			>
				{#if tab.generation.isGenerating}
					<span class="w-1.5 h-1.5 rounded-full bg-warning animate-pulse flex-shrink-0"></span>
				{/if}
				<span class="truncate max-w-[80px]">{tab.name}</span>
				{#if tabs.length > 1}
					<Tooltip text="Close tab" kbd={$shortcutLabels['close_tab']} position="top" delay={150}>
						<button
							type="button"
							class="ml-0.5 p-0.5 rounded-full hover:bg-surface-3/50 {tab.id === activeTabId ? 'hover:bg-accent-hover' : ''}"
							on:click|stopPropagation={() => removeTab(tab.id)}
							aria-label="Close tab"
						>
							<svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
							</svg>
						</button>
					</Tooltip>
				{/if}
			</div>
		{/each}
		<Tooltip text="New tab" kbd={$shortcutLabels['new_tab']} position="top" delay={150}>
			<button
				type="button"
				class="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-full bg-surface-2 text-fg-subtle hover:text-fg transition-colors"
				on:click={addTab}
				aria-label="New tab"
			>
				<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" />
				</svg>
			</button>
		</Tooltip>
	</div>
{/if}

<!-- Generation Tab Bar at Top (Desktop) -->
<div class="tab-bar-container flex-shrink-0 hidden md:block">
	<div class="px-4 h-full">
		<div class="flex items-center h-full gap-4">
			<!-- Left cluster: tabs + add + workspace menu, bottom-aligned within itself -->
			<div class="flex min-w-0 flex-1 items-end self-stretch gap-0 overflow-x-auto no-scrollbar">
				{#each tabs as tab (tab.id)}
					<BookTab
						id={tab.id}
						name={tab.name}
						color={tab.color || null}
						isActive={tab.id === activeTabId}
						canDelete={tabs.length > 1}
						isGenerating={tab.generation.isGenerating}
						on:select={(e) => setActiveTab(e.detail.id)}
						on:rename={handleTabRename}
						on:delete={(e) => removeTab(e.detail.id)}
						on:drop={handleTabReorder}
						on:colorChange={(e) => tabsStore.updateTab(e.detail.id, { color: e.detail.color })}
					/>
				{/each}

				<!-- New Tab Button -->
				<Tooltip text="Add new tab" kbd={$shortcutLabels['new_tab']} position="bottom" delay={150}>
					<button
						class="new-tab-button"
						on:click={addTab}
						type="button"
						aria-label="Add new tab"
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
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
						title="Workspaces"
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
						</svg>
					</button>

					{#if showWorkspaceMenu}
						<!-- Anchored right: the trigger now sits at the right edge of the
						     tab bar (see comment above), so a left-0 dropdown ran off
						     the viewport edge. Opening leftward keeps it on screen. -->
						<div class="absolute right-0 top-full mt-1 z-50 bg-surface-1 border border-line-strong rounded-lg shadow-floating min-w-[260px] py-1">
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

			<!-- Right cluster: overflow only - the session picker lives in the
				generation console bar; Simple/Advanced moved into the
				overflow menu alongside the layout picker. -->
			{#if activeTab}
				<div class="flex flex-shrink-0 items-center gap-2.5">
					<TabsOverflowMenu
						layoutValue={activeTab.layoutMode || 'two'}
						onLayoutChange={handleLayoutChange}
					/>
				</div>
			{/if}
		</div>
	</div>
</div>

<svelte:window on:click={handleWorkspaceClickOutside} />

<!-- Save Workspace Modal -->
<BaseModal
	isOpen={showSaveWorkspaceModal}
	title={workspaceModalMode === 'update' ? 'Update Workspace' : 'Save Workspace'}
	size="sm"
	on:close={() => showSaveWorkspaceModal = false}
>
	<div class="p-6">
		<p class="text-sm text-fg-muted mb-4">
			{workspaceModalMode === 'update'
				? 'Replace this workspace with the current tab layout. You can also rename it.'
				: 'Save the current tab layout (names, colors, presets, modes).'}
		</p>
		<Input
			type="text"
			class="mb-4"
			placeholder="Workspace name..."
			bind:value={workspaceName}
			onkeydown={(e: KeyboardEvent) => e.key === 'Enter' && saveCurrentWorkspace()}
			data-autofocus
		/>
		<div class="flex justify-end gap-2">
			<Button
				variant="ghost"
				size="sm"
				onclick={() => showSaveWorkspaceModal = false}
			>
				Cancel
			</Button>
			<Button
				variant="primary"
				size="sm"
				disabled={!workspaceName.trim() || savingWorkspace}
				loading={savingWorkspace}
				onclick={saveCurrentWorkspace}
			>
				{savingWorkspace
					? workspaceModalMode === 'update' ? 'Updating...' : 'Saving...'
					: workspaceModalMode === 'update' ? 'Update workspace' : 'Save as new'}
			</Button>
		</div>
	</div>
</BaseModal>

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

	.new-tab-button {
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

	.new-tab-button:hover {
		color: rgb(var(--fg-muted));
		background: rgb(var(--surface-3));
		border-color: rgb(var(--line-strong));
		box-shadow: 0 1px 2px rgb(0 0 0 / 0.3);
	}

	.new-tab-button:active {
		transform: scale(0.95);
	}
</style>
