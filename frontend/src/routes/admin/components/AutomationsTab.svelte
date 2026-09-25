<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Button, EmptyState, LoadErrorState, Spinner, Switch } from '$lib/components/ui';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { DataTable, type DataTableColumn } from '$lib/components/table';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import RunStatusBadge from '$lib/components/automation/RunStatusBadge.svelte';
	import { api } from '$lib/services/api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { automationNodeTypes } from '$lib/stores/automationNodeTypes';
	import { adminSectionIcon } from '../adminSections';
	import CreateAutomationModal from '../../automations/components/CreateAutomationModal.svelte';
	import ImportWarningsModal from './ImportWarningsModal.svelte';
	import AutomationDetail from './automations/AutomationDetail.svelte';
	import AutomationTemplateEntryCard from './automations/AutomationTemplateEntryCard.svelte';
	import { AUTOMATIONS_LIBRARY_SECTIONS, type AutomationsLibrarySection } from './automations/automationsLibrarySections';
	import { triggerLabel } from './automations/automationTriggerLabel';
	import { idsNeedingDisable, idsNeedingEnable } from './automations/automationsBulkActions';
	import {
		AUTOMATIONS_SORT_OPTIONS,
		DEFAULT_AUTOMATIONS_FILTERS,
		applyAutomationsFilters,
		type AutomationsFilters,
		type AutomationSortBy
	} from './automations/automationsFilters';
	import type {
		Automation,
		AutomationExportEnvelope,
		AutomationImportWarning,
		AutomationTemplate
	} from '$lib/types/automations';

	let automations = $state<Automation[]>([]);
	let templates = $state<AutomationTemplate[]>([]);
	let loading = $state(true);
	let automationsError = $state<string | null>(null);
	let showCreateModal = $state(false);
	let importInput = $state<HTMLInputElement | null>(null);
	let importing = $state(false);
	let warningsModalAutomation = $state<Automation | null>(null);
	let importWarnings = $state<AutomationImportWarning[]>([]);
	let usingTemplateKey = $state<string | null>(null);
	let selected = $state<Set<string>>(new Set());
	let bulkBusy = $state(false);
	let togglingId = $state<string | null>(null);

	const section = $derived(
		($page.url.searchParams.get('section') as AutomationsLibrarySection) || 'automations'
	);
	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);
	const filters = $derived<AutomationsFilters>({
		q: $page.url.searchParams.get('q') ?? '',
		sortBy: ($page.url.searchParams.get('sortBy') as AutomationSortBy) || DEFAULT_AUTOMATIONS_FILTERS.sortBy
	});

	const sectionCounts = $derived({ automations: automations.length, templates: templates.length });
	const visibleAutomations = $derived(applyAutomationsFilters(automations, filters));
	const visibleTemplates = $derived.by(() => {
		const query = filters.q.trim().toLowerCase();
		if (!query) return templates;
		return templates.filter(
			(template) =>
				template.title.toLowerCase().includes(query) || template.description.toLowerCase().includes(query)
		);
	});

	function buildUrl(
		overrides: { section?: AutomationsLibrarySection; id?: string | null; filters?: AutomationsFilters } = {}
	): string {
		const params = new URLSearchParams();
		params.set('tab', 'automations');
		const nextSection = overrides.section ?? section;
		if (nextSection !== 'automations') params.set('section', nextSection);
		const nextFilters = overrides.filters ?? filters;
		if (nextFilters.q) params.set('q', nextFilters.q);
		if (nextFilters.sortBy !== DEFAULT_AUTOMATIONS_FILTERS.sortBy) params.set('sortBy', nextFilters.sortBy);
		const id = overrides.id !== undefined ? overrides.id : viewId;
		if (id) params.set('id', id);
		const query = params.toString();
		return query ? `${$page.url.pathname}?${query}` : $page.url.pathname;
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;
	function updateFilters(next: AutomationsFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ filters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 200);
	}

	function selectSection(id: AutomationsLibrarySection) {
		selected = new Set();
		void goto(buildUrl({ section: id, id: null }));
	}

	function openAutomationId(id: string) {
		void goto(buildUrl({ id }));
	}

	function backToList() {
		void goto(buildUrl({ id: null }));
	}

	async function loadAutomations() {
		loading = true;
		const [automationsResult, templatesResult] = await Promise.allSettled([
			api.listAutomations(),
			api.listAutomationTemplates()
		]);

		let automationsFailed = true;
		if (automationsResult.status === 'fulfilled' && automationsResult.value.success) {
			automations = automationsResult.value.data ?? [];
			automationsFailed = false;
			automationsError = null;
		} else {
			automationsError =
				automationsResult.status === 'fulfilled'
					? automationsResult.value.message || 'Failed to load automations'
					: getApiErrorMessage(automationsResult.reason, 'Failed to load automations');
		}

		let templatesFailed = true;
		if (templatesResult.status === 'fulfilled' && templatesResult.value.success) {
			templates = templatesResult.value.data ?? [];
			templatesFailed = false;
		}

		if (automationsFailed && templatesFailed) {
			toasts.error('Failed to load automations');
		} else if (templatesFailed) {
			toasts.error('Failed to load automation templates');
		}

		loading = false;
	}

	onMount(() => {
		void loadAutomations();
		void automationNodeTypes.load();
	});

	function handleCreated(event: CustomEvent<Automation>) {
		automations = [event.detail, ...automations];
	}

	async function handleUseTemplate(template: AutomationTemplate) {
		if (!template.available || usingTemplateKey) return;
		usingTemplateKey = template.key;
		try {
			const response = await api.instantiateAutomationTemplate(template.key);
			if (!response.success || !response.data) {
				toasts.error(response.message || response.error || 'Failed to use automation template');
				return;
			}

			const { automation, warnings } = response.data;
			automations = [automation, ...automations];
			if (warnings.length > 0) {
				toasts.warning(
					`Created “${automation.name}” disabled with ${warnings.length} setup warning${warnings.length === 1 ? '' : 's'}`,
					7000
				);
			} else {
				toasts.success(`Created “${automation.name}” disabled`);
			}
			await goto(buildUrl({ section: 'automations', id: automation.id }));
		} catch {
			toasts.error('Failed to use automation template');
		} finally {
			usingTemplateKey = null;
		}
	}

	async function handleToggleEnabled(automation: Automation) {
		togglingId = automation.id;
		try {
			const response = automation.enabled
				? await api.disableAutomation(automation.id)
				: await api.enableAutomation(automation.id);
			if (response.success && response.data) {
				automations = automations.map((a) => (a.id === automation.id ? response.data! : a));
			} else {
				toasts.error(response.error || 'Failed to update automation');
			}
		} catch {
			toasts.error('Failed to update automation');
		} finally {
			togglingId = null;
		}
	}

	function handleAutomationChange(automation: Automation) {
		automations = automations.map((a) => (a.id === automation.id ? automation : a));
	}

	function handleAutomationDeleted(id: string) {
		automations = automations.filter((a) => a.id !== id);
	}

	async function handleBulkEnable() {
		const ids = idsNeedingEnable(automations, selected);
		if (!ids.length) {
			selected = new Set();
			return;
		}
		bulkBusy = true;
		try {
			const results = await Promise.allSettled(ids.map((id) => api.enableAutomation(id)));
			let failed = 0;
			automations = automations.map((a) => {
				const index = ids.indexOf(a.id);
				if (index === -1) return a;
				const result = results[index];
				if (result.status === 'fulfilled' && result.value.success && result.value.data) return result.value.data;
				failed += 1;
				return a;
			});
			if (failed) toasts.error(`Enabled ${ids.length - failed}, failed ${failed}`);
			else toasts.success(`Enabled ${ids.length} automation${ids.length === 1 ? '' : 's'}`);
		} finally {
			bulkBusy = false;
			selected = new Set();
		}
	}

	async function handleBulkDisable() {
		const ids = idsNeedingDisable(automations, selected);
		if (!ids.length) {
			selected = new Set();
			return;
		}
		bulkBusy = true;
		try {
			const results = await Promise.allSettled(ids.map((id) => api.disableAutomation(id)));
			let failed = 0;
			automations = automations.map((a) => {
				const index = ids.indexOf(a.id);
				if (index === -1) return a;
				const result = results[index];
				if (result.status === 'fulfilled' && result.value.success && result.value.data) return result.value.data;
				failed += 1;
				return a;
			});
			if (failed) toasts.error(`Disabled ${ids.length - failed}, failed ${failed}`);
			else toasts.success(`Disabled ${ids.length} automation${ids.length === 1 ? '' : 's'}`);
		} finally {
			bulkBusy = false;
			selected = new Set();
		}
	}

	async function handleBulkDelete() {
		const ids = Array.from(selected);
		if (!ids.length) return;
		if (
			!(await confirmDialog({
				title: `Delete ${ids.length} automation${ids.length === 1 ? '' : 's'}?`,
				message: 'This cannot be undone.',
				variant: 'danger'
			}))
		)
			return;
		bulkBusy = true;
		try {
			const results = await Promise.allSettled(ids.map((id) => api.deleteAutomation(id)));
			const succeeded = new Set(
				ids.filter((_, index) => {
					const result = results[index];
					return result.status === 'fulfilled' && result.value.success;
				})
			);
			automations = automations.filter((a) => !succeeded.has(a.id));
			const failed = ids.length - succeeded.size;
			if (failed) toasts.error(`Deleted ${succeeded.size}, failed ${failed}`);
			else toasts.success(`Deleted ${succeeded.size} automation${succeeded.size === 1 ? '' : 's'}`);
		} finally {
			bulkBusy = false;
			selected = new Set();
		}
	}

	function handleImportClick() {
		importInput?.click();
	}

	async function handleImportFileChange(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;

		importing = true;
		try {
			const text = await file.text();
			let document: AutomationExportEnvelope;
			try {
				document = JSON.parse(text);
			} catch {
				toasts.error('That file is not valid JSON');
				return;
			}

			const response = await api.importAutomation(document);
			if (response.success && response.data) {
				const { automation, warnings } = response.data;
				automations = [automation, ...automations];
				if (warnings.length > 0) {
					warningsModalAutomation = automation;
					importWarnings = warnings;
				} else {
					toasts.success(`Imported "${automation.name}" (disabled)`);
				}
			} else {
				toasts.error(response.message || response.error || 'Failed to import automation');
			}
		} catch {
			toasts.error('Failed to import automation');
		} finally {
			importing = false;
			input.value = '';
		}
	}

	function handleCloseWarningsModal() {
		warningsModalAutomation = null;
		importWarnings = [];
	}

	const columns: DataTableColumn<Automation>[] = $derived.by(() => [
		{ key: 'name', label: 'Name', width: 'minmax(200px,2fr)', cell: nameCell },
		{
			key: 'trigger',
			label: 'Trigger',
			width: 'minmax(140px,1fr)',
			priority: 1,
			accessor: (automation) => triggerLabel(automation.graph, $automationNodeTypes)
		},
		{ key: 'enabled', label: 'Enabled', width: '84px', cell: enabledCell },
		{ key: 'lastRunStatus', label: 'Last run status', width: '140px', priority: 1, cell: lastRunStatusCell },
		{ key: 'lastRun', label: 'Last run', width: '130px', mono: true, priority: 1, cell: lastRunCell }
	]);
</script>

{#snippet nameCell(automation: Automation)}
	<div class="min-w-0">
		<Tooltip text={automation.name}>
			<span class="block truncate font-medium text-fg">{automation.name}</span>
		</Tooltip>
		{#if automation.description}
			<p class="truncate text-xs text-fg-subtle">{automation.description}</p>
		{/if}
	</div>
{/snippet}

{#snippet enabledCell(automation: Automation)}
	<Switch
		label={automation.enabled ? 'Disable automation' : 'Enable automation'}
		checked={automation.enabled}
		busy={togglingId === automation.id}
		onclick={(event) => event.stopPropagation()}
		onchange={() => handleToggleEnabled(automation)}
	/>
{/snippet}

{#snippet lastRunStatusCell(automation: Automation)}
	{#if automation.last_run_status}
		<RunStatusBadge status={automation.last_run_status} />
	{:else}
		<span class="text-fg-subtle">—</span>
	{/if}
{/snippet}

{#snippet lastRunCell(automation: Automation)}
	{#if automation.last_run_at}
		<Tooltip text={new Date(automation.last_run_at).toLocaleString()}>
			<span>{timeAgo(automation.last_run_at)}</span>
		</Tooltip>
	{:else}
		<span class="text-fg-subtle">Never</span>
	{/if}
{/snippet}

{#snippet importOverflowMenu(close: () => void)}
	<button
		type="button"
		role="menuitem"
		class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-fg hover:bg-surface-3 disabled:opacity-50"
		disabled={importing}
		onclick={() => {
			close();
			handleImportClick();
		}}
	>
		<Icon name="upload" className="w-3.5 h-3.5" />
		Import…
	</button>
{/snippet}

<LibraryShell
	title="Automations"
	persistKey="admin-automations-library"
	heightClass="h-full"
	sections={AUTOMATIONS_LIBRARY_SECTIONS}
	{section}
	onSelectSection={selectSection}
	{sectionCounts}
	count={section === 'templates' ? visibleTemplates.length : visibleAutomations.length}
	{detailOpen}
	overflow={section === 'automations' ? importOverflowMenu : undefined}
>
	{#snippet toolbar()}
		<LibraryFilterBar
			q={filters.q}
			onQueryChange={(value) => updateFilters({ ...filters, q: value })}
			searchPlaceholder={section === 'templates' ? 'Search templates…' : 'Search automations by name or description…'}
			sortBy={filters.sortBy}
			sortOptions={AUTOMATIONS_SORT_OPTIONS}
			onSortChange={(value) => updateFilters({ ...filters, sortBy: value as AutomationSortBy })}
		/>
	{/snippet}

	{#snippet primary()}
		{#if section === 'automations'}
			<Button variant="primary" size="sm" icon="plus" onclick={() => (showCreateModal = true)}>
				New automation
			</Button>
		{/if}
	{/snippet}

	<input
		bind:this={importInput}
		type="file"
		accept="application/json,.json"
		class="hidden"
		onchange={handleImportFileChange}
	/>

	{#if detailOpen}
		{#key viewId}
			<AutomationDetail
				automationId={viewId as string}
				onBack={backToList}
				onDeleted={handleAutomationDeleted}
				onAutomationChange={handleAutomationChange}
			/>
		{/key}
	{:else if section === 'templates'}
		<div class="h-full overflow-y-auto p-4">
			{#if loading}
				<div class="flex h-40 items-center justify-center">
					<Spinner size="lg" />
				</div>
			{:else if visibleTemplates.length === 0}
				<div class="flex h-full items-center justify-center">
					<EmptyState
						icon="copy"
						title={templates.length === 0 ? 'No templates available' : 'No matching templates'}
						description={templates.length === 0
							? 'Enable a plugin that contributes automation templates to see ready-made workflows here.'
							: 'Try a different search term.'}
						compact
					/>
				</div>
			{:else}
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
					{#each visibleTemplates as template (template.key)}
						<AutomationTemplateEntryCard
							{template}
							loading={usingTemplateKey === template.key}
							onUse={handleUseTemplate}
						/>
					{/each}
				</div>
			{/if}
		</div>
	{:else if automationsError && automations.length === 0}
		<LoadErrorState message={automationsError} onRetry={loadAutomations} retrying={loading} />
	{:else}
		<div class="flex flex-col gap-3 p-4">
			<SelectionActionBar
				active={selected.size > 0}
				selectedCount={selected.size}
				totalCount={visibleAutomations.length}
				onSelectAll={() => (selected = selectPage(selected, visibleAutomations.map((a) => a.id)))}
				onClearSelection={() => (selected = clearAll())}
				onClose={() => (selected = clearAll())}
			>
				<svelte:fragment slot="actionsBeforeCollection">
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors disabled:opacity-50"
						disabled={bulkBusy}
						onclick={handleBulkEnable}
					>
						Enable
					</button>
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors disabled:opacity-50"
						disabled={bulkBusy}
						onclick={handleBulkDisable}
					>
						Disable
					</button>
					<button
						class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors font-medium disabled:opacity-50"
						disabled={bulkBusy}
						onclick={handleBulkDelete}
					>
						Delete
					</button>
				</svelte:fragment>
			</SelectionActionBar>

			<DataTable
				{columns}
				rows={visibleAutomations}
				getRowId={(automation) => automation.id}
				onRowClick={(automation) => openAutomationId(automation.id)}
				{selected}
				onSelectedChange={(next) => (selected = next)}
				loading={loading && automations.length === 0}
				isFiltered={visibleAutomations.length !== automations.length}
			>
				{#snippet emptyState()}
					<EmptyState
						icon={adminSectionIcon('automations')}
						title="No automations yet"
						description="An automation runs a workflow for you automatically — on a schedule or when something happens — so you don't have to trigger it by hand."
						compact
					>
						{#snippet actions()}
							<Button variant="primary" icon="plus" size="sm" onclick={() => (showCreateModal = true)}>
								Create new automation
							</Button>
							<Button variant="secondary" icon="copy" size="sm" onclick={() => selectSection('templates')}>
								Browse templates
							</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState
						icon="search"
						title="No matching automations"
						description="Try a different search term."
						compact
					>
						{#snippet actions()}
							<Button variant="ghost" size="sm" onclick={() => updateFilters(DEFAULT_AUTOMATIONS_FILTERS)}>
								Clear search
							</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
			</DataTable>
		</div>
	{/if}
</LibraryShell>

<CreateAutomationModal
	isOpen={showCreateModal}
	on:close={() => (showCreateModal = false)}
	on:created={handleCreated}
/>

<ImportWarningsModal
	isOpen={warningsModalAutomation !== null}
	automationName={warningsModalAutomation?.name ?? ''}
	warnings={importWarnings}
	on:close={handleCloseWarningsModal}
/>
