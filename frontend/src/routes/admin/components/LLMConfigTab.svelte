<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, Badge, Spinner, EmptyState, Input, IconButton } from '$lib/components/ui';
	import { MasterDetailLayout, DetailEmptyState } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import { DetailHeader, DetailTabs, DetailBody, DetailFooter } from '$lib/components/detail';
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

	$: llmDetailTabs = activeConfig
		? [
				{ id: 'configuration', label: 'Configuration', icon: 'sliders' },
				{ id: 'toolset', label: 'Toolset', icon: 'shield' },
				{
					id: 'access',
					label: 'Access',
					icon: 'group',
					count: (assignmentSummary[activeConfig.id]?.assignment_count || 0) + (assignmentSummary[activeConfig.id]?.group_count || 0)
				}
			]
		: [];

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
						<DetailHeader title={activeConfig.name}>
							{#snippet subtitle()}
								{activeConfig.id}
							{/snippet}
							{#snippet chips()}
								<Badge variant={editFormData.enabled ? 'success' : 'neutral'} dot>
									{editFormData.enabled ? 'Enabled' : 'Disabled'}
								</Badge>
							{/snippet}
							{#snippet actions()}
								<Tooltip text="Duplicate configuration">
									<IconButton icon="copy" label="Duplicate configuration" onclick={() => handleDuplicateConfig(activeConfig)} />
								</Tooltip>
								<Tooltip text="Delete configuration">
									<IconButton
										icon="trash"
										label="Delete configuration"
										class="text-danger hover:text-danger hover:bg-danger/10"
										onclick={() => handleDeleteConfig(activeConfig.id)}
									/>
								</Tooltip>
							{/snippet}
						</DetailHeader>

						<DetailTabs tabs={llmDetailTabs} active={detailTab} onSelect={(id) => (detailTab = id as typeof detailTab)} ariaLabel="LLM configuration details" />

						{#if detailTab === 'configuration'}
							<DetailBody>
								<LLMConfigForm
									bind:draft={editFormData}
									mode="edit"
									layout="panel"
									idPrefix="edit-config"
									apiKeySet={!!activeConfig?.api_key_set}
									{preChatActions}
								/>
							</DetailBody>
						{:else if detailTab === 'toolset'}
							<DetailBody>
								{#key activeConfig.id}
									<LLMConfigToolsetPanel configId={activeConfig.id} />
								{/key}
							</DetailBody>
						{:else}
							<DetailBody>
								{#key activeConfig.id}
									<AssignmentCard
										adapter={createLLMAssignmentAdapter(activeConfig.id)}
										resourceKey={activeConfig.id}
										resourceName={activeConfig.name}
										on:changed={(event) => handleAssignmentChanged(activeConfig.id, event)}
									/>
								{/key}
							</DetailBody>
						{/if}

						<DetailFooter dirtyCount={editDirty ? 1 : 0} dirtyLabel={editDirty ? 'Unsaved changes' : undefined}>
							<Button variant="ghost" size="sm" disabled={!editDirty} onclick={discardEditForm}>Discard</Button>
							<Button variant="primary" size="sm" loading={editSaving} disabled={!editDirty} onclick={saveEditForm}>Save</Button>
						</DetailFooter>
					{:else}
						<DetailEmptyState message="Select a configuration to view its details" icon="document" />
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
