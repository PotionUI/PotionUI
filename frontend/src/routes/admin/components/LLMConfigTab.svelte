<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Badge, Spinner, EmptyState, Input } from '$lib/components/ui';
	import { MasterDetailLayout } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import LLMConfigForm, { type LLMConfigFormData } from './LLMConfigForm.svelte';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import { createLLMAssignmentAdapter } from '$lib/components/assignment/llmAssignmentAdapter';
	import LLMConfigToolsetPanel from './LLMConfigToolsetPanel.svelte';
	import type { PreChatAction } from '$lib/types/llm';
	import type { AssignmentSummary } from '$lib/services/admin-api';

	let configurations: any[] = [];
	let loading = true;
	let searchQuery = '';
	let selectedConfigId: string | null = null;
	let showConfigModal = false;
	let preChatActions: PreChatAction[] = [];
	let assignmentSummary: AssignmentSummary = {};
	let detailTab: 'configuration' | 'toolset' | 'access' = 'configuration';

	const defaultSystemMessage = `You are a helpful AI assistant specialized in generating image prompts and tags.
When asked for prompts, provide clear, descriptive, and well-structured responses.
Format your responses appropriately for the context requested.

Examples:
- For danbooru tags: provide comma-separated tags
- For descriptive prompts: provide flowing, descriptive text
- For style suggestions: focus on artistic styles, techniques, and aesthetics

Always be creative and helpful while staying focused on the image generation context.`;

	/**
	 * Build a form draft from a stored config (or `null` for a blank one), with optional overrides.
	 *
	 * The API key is never part of a stored config's response (see `api_key_set`
	 * on the config object) — the draft always starts blank, and an edit only
	 * sends a replacement key when the admin types one in.
	 */
	function configFormFrom(config: any, overrides: Partial<LLMConfigFormData> = {}): LLMConfigFormData {
		return {
			name: config?.name ?? '',
			type: config?.type ?? 'openai',
			model: config?.model ?? '',
			api_key: '',
			base_url: config?.base_url || '',
			enabled: config?.enabled ?? true,
			supports_vision: config?.supports_vision || false,
			disable_system_prompt: config?.disable_system_prompt || false,
			memory_reflection: config?.memory_reflection ?? true,
			system_message: config?.system_message || defaultSystemMessage,
			temperature: config?.temperature || 0.7,
			max_tokens: config?.max_tokens || 1000,
			timeout: config?.timeout || 30,
			provider_options: config?.provider_options ? { ...config.provider_options } : {},
			...overrides
		};
	}

	// Create-modal form (new configuration or a duplicate — the modal never edits an existing one).
	let configFormData: LLMConfigFormData = configFormFrom(null);

	// Detail-pane edit form: a live draft for the selected configuration. Editing
	// happens directly in the pane, snapshotted on load/save/discard to drive
	// `editDirty` without a heavier dirty-tracking system.
	let editFormData: LLMConfigFormData = configFormFrom(null);
	let editSnapshot = JSON.stringify(editFormData);
	let editSaving = false;

	$: enabledCount = configurations.filter(c => c.enabled).length;
	$: filteredConfigurations = configurations.filter((c) => {
		const q = searchQuery.trim().toLowerCase();
		if (!q) return true;
		return c.name?.toLowerCase().includes(q) || c.model?.toLowerCase().includes(q);
	});
	// Derived from the full (unfiltered) list so the detail pane keeps showing
	// the selected item even while a search hides it from the list.
	$: activeConfig = configurations.find((c) => c.id === selectedConfigId) ?? null;

	// True once the pane's draft diverges from the last loaded/saved snapshot.
	$: editDirty = JSON.stringify(editFormData) !== editSnapshot;

	onMount(async () => {
		await loadConfigurations();
		await loadPreChatActions();
		await loadAssignmentSummary();
	});

	async function loadAssignmentSummary() {
		try {
			const response = await adminApi.getLLMAssignmentSummary();
			if (response.success && response.data) {
				assignmentSummary = response.data;
			}
		} catch (error) {
			logger.error('Failed to load LLM assignment summary:', error);
		}
	}

	function handleAssignmentChanged(configId: string, event: CustomEvent<{ userCount: number; groupCount: number }>) {
		assignmentSummary = {
			...assignmentSummary,
			[configId]: { assignment_count: event.detail.userCount, group_count: event.detail.groupCount }
		};
	}

	async function loadConfigurations() {
		try {
			loading = true;
			const response = await api.getLLMConfigurations();
			if (response.success && response.data) {
				configurations = response.data.configurations || [];
			}
		} catch (error) {
			logger.error('Failed to load LLM configurations:', error);
		} finally {
			loading = false;
		}
	}

	async function loadPreChatActions() {
		try {
			const response = await api.getPreChatActions();
			if (response.success && response.data) {
				preChatActions = response.data.actions || [];
			}
		} catch (error) {
			logger.error('Failed to load pre-chat actions:', error);
		}
	}

	function handleCreateConfig() {
		configFormData = configFormFrom(null);
		showConfigModal = true;
	}

	function handleDuplicateConfig(config: any) {
		configFormData = configFormFrom(config, { name: `${config.name} (copy)`, enabled: false });
		showConfigModal = true;
	}

	// Create a new configuration (or a duplicate) from the modal form.
	async function handleSaveConfig() {
		try {
			await adminApi.createLLMConfiguration(configFormData);
			await loadConfigurations();
			showConfigModal = false;
		} catch (error) {
			logger.error('Failed to save LLM configuration:', error);
			toasts.error('Failed to save LLM configuration');
		}
	}

	// Select a configuration for the detail pane, loading its edit draft. If the
	// current draft has unsaved changes, confirm before discarding them —
	// a lightweight guard rather than a full dirty-tracking system.
	async function selectConfig(id: string) {
		if (editDirty && !(await confirmDialog({
			title: 'Discard unsaved changes',
			message: 'Discard unsaved changes to this configuration?',
			variant: 'warning'
		}))) return;
		selectedConfigId = id;
		detailTab = 'configuration';
		loadEditForm(configurations.find((c) => c.id === id) ?? null);
	}

	function loadEditForm(config: any | null) {
		editFormData = configFormFrom(config);
		editSnapshot = JSON.stringify(editFormData);
	}

	function discardEditForm() {
		loadEditForm(activeConfig);
	}

	// Save the detail pane's edit draft.
	async function saveEditForm() {
		if (!activeConfig) return;
		editSaving = true;
		try {
			await adminApi.updateLLMConfiguration(activeConfig.id, editFormData);
			toasts.success(`${editFormData.name || activeConfig.name} updated`);
			await loadConfigurations();
			loadEditForm(configurations.find((c) => c.id === activeConfig!.id) ?? null);
		} catch (error) {
			logger.error('Failed to save LLM configuration:', error);
			toasts.error('Failed to save LLM configuration');
		} finally {
			editSaving = false;
		}
	}

	async function handleDeleteConfig(configId: string) {
		if (!(await confirmDialog({
			title: 'Delete LLM configuration',
			message: 'Are you sure you want to delete this LLM configuration?',
			variant: 'danger'
		}))) return;

		try {
			await adminApi.deleteLLMConfiguration(configId);
			const wasSelected = selectedConfigId === configId;
			await loadConfigurations();
			if (wasSelected) {
				selectedConfigId = null;
				loadEditForm(null);
			}
		} catch (error) {
			logger.error('Failed to delete LLM configuration:', error);
			toasts.error('Failed to delete LLM configuration');
		}
	}
</script>

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="LLM Configurations"
		icon="model"
		counts={[
			{ label: 'total', value: configurations.length },
			{ label: 'enabled', value: enabledCount, tone: 'success' }
		]}
	>
		{#snippet actions()}
			<Button variant="primary" size="sm" icon="plus" onclick={handleCreateConfig}>
				Add Configuration
			</Button>
		{/snippet}
	</AdminTabShell>

	{#snippet configSearch()}
		<div class="relative">
			<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
			<Input bind:value={searchQuery} type="search" class="pl-9" placeholder="Search by name or model…" aria-label="Search LLM configurations" />
		</div>
	{/snippet}
	{#snippet configSearchTrailing()}
		<span class="text-sm text-fg-muted whitespace-nowrap font-mono tabular-nums">{filteredConfigurations.length} {filteredConfigurations.length === 1 ? 'configuration' : 'configurations'}</span>
	{/snippet}

	<AdminFilterBar
		search={configSearch}
		trailing={configSearchTrailing}
		activeCount={searchQuery ? 1 : 0}
		onClear={() => (searchQuery = '')}
	/>

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		{#if loading}
			<div class="h-full flex flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading configurations…</p>
			</div>
		{:else if configurations.length === 0}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState
					icon="model"
					title="No LLM configurations yet"
					description="Connect an LLM provider to power AI features like prompt generation, chat, and content improvement."
					compact
				>
					{#snippet actions()}
						<Button variant="primary" size="sm" icon="plus" onclick={handleCreateConfig}>
							Add Configuration
						</Button>
					{/snippet}
				</EmptyState>
			</div>
		{:else}
			<MasterDetailLayout leftWidth={340} minWidth={280} maxWidth={480} storageKey="admin-llm-config-width">
				<div slot="list" class="h-full min-h-0">
					<Pane
						label="Configurations"
						count={filteredConfigurations.length}
						isEmpty={filteredConfigurations.length === 0}
						bodyRole="listbox"
						ariaLabel="LLM configurations"
					>
						{#snippet empty()}
							<div class="p-4 h-full flex items-center justify-center">
								<EmptyState icon="search" title="No matches" description="Try a different name or model." compact>
									{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => (searchQuery = '')}>Clear search</Button>{/snippet}
								</EmptyState>
							</div>
						{/snippet}

						{#snippet children()}
							{#each filteredConfigurations as config (config.id)}
								{#snippet configLeading()}
									<span
										class="w-2 h-2 rounded-full flex-shrink-0 {config.enabled ? 'bg-success-solid' : 'bg-line-strong'}"
										title={config.enabled ? 'Enabled' : 'Disabled'}
									></span>
								{/snippet}
								{#snippet configBadges()}
									{#if config.supports_vision}
										<Badge variant="warning" size="sm">Vision</Badge>
									{/if}
									{#if !(assignmentSummary[config.id]?.assignment_count || 0) && !(assignmentSummary[config.id]?.group_count || 0)}
										<span title="Only admins can see this — assign users or groups">
											<Badge variant="warning" size="sm">Unassigned</Badge>
										</span>
									{/if}
								{/snippet}
								<PaneRow
									selected={selectedConfigId === config.id}
									onclick={() => selectConfig(config.id)}
									leading={configLeading}
									title={config.name}
									subtitle="{config.type} · {config.model}"
									subtitleMono
									badges={configBadges}
								/>
							{/each}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					{#if activeConfig}
						<div class="flex flex-wrap items-center justify-between gap-3 px-4 sm:px-5 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
							<div class="min-w-0">
								<h2 class="text-base font-semibold text-fg truncate">{activeConfig.name}</h2>
								<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{activeConfig.id}</p>
							</div>
							<div class="flex items-center gap-2 flex-shrink-0 flex-wrap">
								<Badge variant={editFormData.enabled ? 'success' : 'neutral'} dot>
									{editFormData.enabled ? 'Enabled' : 'Disabled'}
								</Badge>
								<Button variant="secondary" size="sm" onclick={() => handleDuplicateConfig(activeConfig)} title="Duplicate configuration">
									Duplicate
								</Button>
								<Button
									variant="secondary"
									size="sm"
									icon="trash"
									class="text-danger hover:text-danger"
									title="Delete configuration"
									onclick={() => handleDeleteConfig(activeConfig.id)}
								/>
								<div class="h-5 w-px bg-line-strong mx-1"></div>
								{#if editDirty}<Badge variant="warning" size="sm" dot>Unsaved</Badge>{/if}
								<Button variant="ghost" size="sm" disabled={!editDirty} onclick={discardEditForm}>Discard</Button>
								<Button variant="primary" size="sm" loading={editSaving} disabled={!editDirty} onclick={saveEditForm}>Save</Button>
							</div>
						</div>

						<div class="flex flex-wrap items-center gap-2 px-4 sm:px-5 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
							<nav class="inline-flex items-center gap-1" aria-label="LLM configuration details">
								<button
									type="button"
									class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'configuration' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
									on:click={() => (detailTab = 'configuration')}
									aria-current={detailTab === 'configuration' ? 'page' : undefined}
								><Icon name="sliders" className="w-3.5 h-3.5" />Configuration</button>
								<button
									type="button"
									class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'toolset' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
									on:click={() => (detailTab = 'toolset')}
									aria-current={detailTab === 'toolset' ? 'page' : undefined}
								><Icon name="shield" className="w-3.5 h-3.5" />Toolset</button>
								<button
									type="button"
									class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'access' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
									on:click={() => (detailTab = 'access')}
									aria-current={detailTab === 'access' ? 'page' : undefined}
								>
									<Icon name="group" className="w-3.5 h-3.5" />Access
									<span class="font-mono text-2xs opacity-70">{(assignmentSummary[activeConfig.id]?.assignment_count || 0) + (assignmentSummary[activeConfig.id]?.group_count || 0)}</span>
								</button>
							</nav>
						</div>

						<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2 p-4 sm:p-5">
							{#if detailTab === 'configuration'}
								<!-- Section cards matching System Settings' panel style — a WQHD
								     pane keeps the control right under its label instead of stranded
								     far to the right. Capped to a readable column so controls never
								     stretch across an ultrawide pane. -->
								<div class="max-w-2xl">
									<LLMConfigForm
										bind:draft={editFormData}
										mode="edit"
										layout="panel"
										idPrefix="edit-config"
										apiKeySet={!!activeConfig?.api_key_set}
										{preChatActions}
									/>
								</div>
							{:else if detailTab === 'toolset'}
								<div class="max-w-2xl">
									{#key activeConfig.id}
										<LLMConfigToolsetPanel configId={activeConfig.id} />
									{/key}
								</div>
							{:else}
								<div class="max-w-3xl">
									{#key activeConfig.id}
										<AssignmentCard
											adapter={createLLMAssignmentAdapter(activeConfig.id)}
											resourceKey={activeConfig.id}
											resourceName={activeConfig.name}
											on:changed={(event) => handleAssignmentChanged(activeConfig.id, event)}
										/>
									{/key}
								</div>
							{/if}
						</div>
					{:else}
						<div class="h-full p-5 flex items-center justify-center">
							<EmptyState
								title="No configuration selected"
								description="Choose an LLM configuration from the list to see and edit it."
								icon="model"
								compact
							/>
						</div>
					{/if}
				</div>
			</MasterDetailLayout>
		{/if}
	</section>
</div>

<!-- Create/Duplicate Config Modal (create only — editing an existing configuration happens directly in the detail pane) -->
<BaseModal
	isOpen={showConfigModal}
	title="Create LLM Configuration"
	size="lg"
	on:close={() => showConfigModal = false}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="model" className="w-5 h-5 text-fg-muted" />
	</svelte:fragment>

	<div class="p-6">
		<LLMConfigForm
			bind:draft={configFormData}
			mode="create"
			layout="plain"
			idPrefix="create-config"
			{preChatActions}
		/>
	</div>

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-3 px-6 py-4">
			<Button variant="secondary" onclick={() => showConfigModal = false}>
				Cancel
			</Button>
			<Button
				variant="primary"
				disabled={!configFormData.name || !configFormData.model}
				onclick={handleSaveConfig}
			>
				Create
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
