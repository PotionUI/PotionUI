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
	import { Button, Badge, EmptyState, IconButton, Switch } from '$lib/components/ui';
	import { DetailHeader, DetailTabs, DetailBody, DetailLayout, DetailFooter } from '$lib/components/detail';
	import { DataTable, StatusCell } from '$lib/components/table';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import LLMConfigForm, { type LLMConfigFormData } from './LLMConfigForm.svelte';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import { createLLMAssignmentAdapter } from '$lib/components/assignment/llmAssignmentAdapter';
	import LLMConfigToolsetPanel from './LLMConfigToolsetPanel.svelte';
	import type { PreChatAction } from '$lib/types/llm';
	import type { AssignmentSummary } from '$lib/services/admin-api';
	import { adminSectionIcon } from '../adminSections';
	import { applyLLMConfigFilters, DEFAULT_LLM_CONFIG_FILTERS, type LLMConfigFilters } from './llmConfigFilters';
	import { llmConfigDetailTabHasFooter, type LLMConfigDetailTab } from './llmConfigDetailTabs';
	import { assignmentCountLabel } from './llm/llmConfigColumns';
	import { summarizeBulkOutcome, bulkOutcomeMessage } from './llm/bulkResult';

	let {
		filters = DEFAULT_LLM_CONFIG_FILTERS,
		total = $bindable(0),
		visibleCount = $bindable(0),
		detailOpen = $bindable(false)
	}: {
		filters?: LLMConfigFilters;
		total?: number;
		visibleCount?: number;
		detailOpen?: boolean;
	} = $props();

	let configurations = $state<any[]>([]);
	let loading = $state(true);
	let selectedConfigId = $state<string | null>(null);
	let showConfigModal = $state(false);
	let preChatActions = $state<PreChatAction[]>([]);
	let assignmentSummary = $state<AssignmentSummary>({});
	let detailTab = $state<LLMConfigDetailTab>('configuration');
	let selected = $state<Set<string>>(new Set());
	let togglingEnabled = $state(false);

	const defaultSystemMessage = `You are a helpful AI assistant specialized in generating image prompts and tags.
When asked for prompts, provide clear, descriptive, and well-structured responses.
Format your responses appropriately for the context requested.

Examples:
- For danbooru tags: provide comma-separated tags
- For descriptive prompts: provide flowing, descriptive text
- For style suggestions: focus on artistic styles, techniques, and aesthetics

Always be creative and helpful while staying focused on the image generation context.`;

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
			system_message: config?.system_message ?? defaultSystemMessage,
			temperature: config?.temperature ?? 0.7,
			max_tokens: config?.max_tokens || 1000,
			timeout: config?.timeout || 30,
			provider_options: config?.provider_options ? { ...config.provider_options } : {},
			...overrides
		};
	}

	let configFormData = $state<LLMConfigFormData>(configFormFrom(null));
	let editFormData = $state<LLMConfigFormData>(configFormFrom(null));
	let editSnapshot = $state(JSON.stringify(editFormData));
	let editSaving = $state(false);

	const filteredConfigurations = $derived(applyLLMConfigFilters(configurations, filters));
	const activeConfig = $derived(configurations.find((c) => c.id === selectedConfigId) ?? null);
	const editDirty = $derived(JSON.stringify(editFormData) !== editSnapshot);
	const llmDetailTabs = $derived(
		activeConfig
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
			: []
	);

	$effect(() => {
		total = configurations.length;
		visibleCount = filteredConfigurations.length;
		detailOpen = !!activeConfig;
	});

	export function openCreateModal() {
		handleCreateConfig();
	}

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

	async function backToList() {
		if (editDirty && !(await confirmDialog({
			title: 'Discard unsaved changes',
			message: 'Discard unsaved changes to this configuration?',
			variant: 'warning'
		}))) return;
		selectedConfigId = null;
	}

	function loadEditForm(config: any | null) {
		editFormData = configFormFrom(config);
		editSnapshot = JSON.stringify(editFormData);
	}

	function discardEditForm() {
		loadEditForm(activeConfig);
	}

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

	async function toggleEnabledImmediate(next: boolean) {
		if (!activeConfig) return;
		togglingEnabled = true;
		try {
			await adminApi.updateLLMConfiguration(activeConfig.id, configFormFrom(activeConfig, { enabled: next }));
			toasts.success(`${activeConfig.name} ${next ? 'enabled' : 'disabled'}`);
			editFormData = { ...editFormData, enabled: next };
			editSnapshot = JSON.stringify({ ...JSON.parse(editSnapshot), enabled: next });
			await loadConfigurations();
		} catch (error) {
			logger.error('Failed to update configuration:', error);
			toasts.error('Failed to update configuration');
		} finally {
			togglingEnabled = false;
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

	async function bulkSetEnabled(next: boolean) {
		const rows = configurations.filter((c) => selected.has(c.id));
		if (!rows.length) return;
		const results = await Promise.allSettled(
			rows.map((row) => adminApi.updateLLMConfiguration(row.id, configFormFrom(row, { enabled: next })))
		);
		const outcome = summarizeBulkOutcome(results);
		const { ok, text } = bulkOutcomeMessage(outcome, next ? 'enabled' : 'disabled', 'configuration');
		if (text) (ok ? toasts.success : toasts.error)(text);
		selected = new Set();
		await loadConfigurations();
	}

	async function bulkDelete() {
		const ids = [...selected];
		if (!ids.length) return;
		if (!(await confirmDialog({
			title: `Delete ${ids.length} configuration${ids.length === 1 ? '' : 's'}?`,
			message: 'This cannot be undone.',
			variant: 'danger'
		}))) return;
		const results = await Promise.allSettled(ids.map((id) => adminApi.deleteLLMConfiguration(id)));
		const outcome = summarizeBulkOutcome(results);
		const { ok, text } = bulkOutcomeMessage(outcome, 'deleted', 'configuration');
		if (text) (ok ? toasts.success : toasts.error)(text);
		selected = new Set();
		await loadConfigurations();
	}
</script>

{#snippet enabledCell(row: any)}
	<StatusCell tone={row.enabled ? 'success' : 'muted'} label={row.enabled ? 'Enabled' : 'Disabled'} />
{/snippet}

{#snippet assignedCell(row: any)}
	{@const label = assignmentCountLabel(assignmentSummary, row.id)}
	{#if label === 'Unassigned'}
		<Tooltip text="Only admins can see this — assign users or groups">
			<span class="font-mono text-xs text-warning">Unassigned</span>
		</Tooltip>
	{:else}
		<span class="font-mono text-xs tabular-nums text-fg-muted">{label}</span>
	{/if}
{/snippet}

<div class="h-full min-h-0 flex flex-col">
	{#if activeConfig}
		<DetailHeader title={activeConfig.name} icon={adminSectionIcon('llm')} backLabel="Configurations" onBack={backToList}>
			{#snippet subtitle()}{activeConfig.type} · {activeConfig.model}{/snippet}
			{#snippet chips()}
				{#if activeConfig.supports_vision}<Badge size="sm" variant="warning">Vision</Badge>{/if}
				{#if activeConfig.is_default}<Badge size="sm" variant="signal">Default</Badge>{/if}
			{/snippet}
			{#snippet enabledSwitch()}
				<label class="flex items-center gap-1.5">
					<span class="text-2xs text-fg-subtle">Enabled</span>
					<Switch label="Enabled" checked={activeConfig.enabled} busy={togglingEnabled} onchange={toggleEnabledImmediate} />
				</label>
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

		<DetailTabs tabs={llmDetailTabs} active={detailTab} onSelect={(id) => (detailTab = id as LLMConfigDetailTab)} ariaLabel="LLM configuration details" />

		{#if detailTab === 'configuration'}
			<DetailBody>
				<DetailLayout>
					{#snippet main()}
						<LLMConfigForm
							bind:draft={editFormData}
							mode="edit"
							layout="panel"
							idPrefix="edit-config"
							apiKeySet={!!activeConfig?.api_key_set}
							{preChatActions}
						/>
					{/snippet}
				</DetailLayout>
			</DetailBody>
		{:else if detailTab === 'toolset'}
			<DetailBody>
				<DetailLayout>
					{#snippet main()}
						{#key activeConfig.id}
							<LLMConfigToolsetPanel configId={activeConfig.id} />
						{/key}
					{/snippet}
				</DetailLayout>
			</DetailBody>
		{:else}
			<DetailBody>
				<DetailLayout>
					{#snippet main()}
						{#key activeConfig.id}
							<AssignmentCard
								adapter={createLLMAssignmentAdapter(activeConfig.id)}
								resourceKey={activeConfig.id}
								resourceName={activeConfig.name}
								on:changed={(event) => handleAssignmentChanged(activeConfig.id, event)}
							/>
						{/key}
					{/snippet}
				</DetailLayout>
			</DetailBody>
		{/if}

		{#if llmConfigDetailTabHasFooter(detailTab)}
			<DetailFooter
				dirtyCount={editDirty ? 1 : 0}
				saving={editSaving}
				onSave={saveEditForm}
				onDiscard={discardEditForm}
			/>
		{/if}
	{:else}
		<div class="flex flex-col gap-3 p-4">
			<SelectionActionBar
				active={selected.size > 0}
				selectedCount={selected.size}
				totalCount={filteredConfigurations.length}
				onSelectAll={() => (selected = selectPage(selected, filteredConfigurations.map((c) => c.id)))}
				onClearSelection={() => (selected = clearAll())}
				onClose={() => (selected = clearAll())}
			>
				<svelte:fragment slot="actionsBeforeCollection">
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
						onclick={() => bulkSetEnabled(true)}
					>
						Enable
					</button>
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
						onclick={() => bulkSetEnabled(false)}
					>
						Disable
					</button>
					<button
						class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors font-medium"
						onclick={bulkDelete}
					>
						Delete
					</button>
				</svelte:fragment>
			</SelectionActionBar>
			<DataTable
				columns={[
					{ key: 'name', label: 'Name', width: 'minmax(160px,1.6fr)', accessor: (r) => r.name },
					{ key: 'type', label: 'Provider', width: '110px', accessor: (r) => r.type },
					{ key: 'model', label: 'Model', width: 'minmax(140px,1.4fr)', mono: true, accessor: (r) => r.model },
					{ key: 'enabled', label: 'Enabled', width: '110px', cell: enabledCell },
					{ key: 'supports_vision', label: 'Vision', width: '90px', priority: 1, accessor: (r) => (r.supports_vision ? 'Vision' : null) },
					{ key: 'assigned', label: 'Assigned', width: '180px', priority: 1, cell: assignedCell },
					{ key: 'is_default', label: 'Default', width: '100px', priority: 1, accessor: (r) => (r.is_default ? 'Default' : null) }
				]}
				rows={filteredConfigurations}
				getRowId={(r) => r.id}
				{loading}
				selected={selected}
				onSelectedChange={(next) => (selected = next)}
				onRowClick={(r) => selectConfig(r.id)}
				isFiltered={!!filters.q.trim()}
			>
				{#snippet emptyState()}
					<EmptyState
						icon="model"
						title="No LLM configurations yet"
						description="Connect an LLM provider to power AI features like prompt generation, chat, and content improvement."
						compact
					>
						{#snippet actions()}
							<Button variant="primary" size="sm" icon="plus" onclick={handleCreateConfig}>Add configuration</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState icon="search" title="No matches" description="Try a different name or model." compact />
				{/snippet}
			</DataTable>
		</div>
	{/if}
</div>

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
