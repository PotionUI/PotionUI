<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { isAxiosError } from 'axios';
	import { logger, getErrorMessage, getApiErrorMessage } from '$lib/utils/logger';
	import type { EngineDescriptor, EngineField, IndexModelsResult, BackendStats } from '$lib/services/admin-api';
	import {
		indexBackendModels,
		getBackendStats,
		getBackendEngines,
		getBackends,
		getAllBackendsHealth,
		createBackend,
		updateBackend,
		deleteBackend as deleteBackendRequest,
		setDefaultBackend,
		testBackend
	} from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { adminWebSocket } from '$lib/services/adminWebsocket';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { Button, Badge, Spinner, EmptyState, Switch, Alert, IconButton } from '$lib/components/ui';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import BackendForm from './BackendForm.svelte';
	import { DetailHeader, DetailTabs, DetailBody, DetailLayout, DetailSection, KVGrid, KVItem, DetailFooter } from '$lib/components/detail';
	import type { DetailHeaderChip, DetailHeaderChipTone } from '$lib/components/detail/detailHeaderChips';
	import { DataTable, StatusCell, type DataTableColumn } from '$lib/components/table';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import { PaneRow } from '$lib/components/pane';
	import { adminSectionIcon } from '../adminSections';
	import type { Backend, BackendHealth } from '$lib/services/admin-api';
	import {
		BACKENDS_SORT_OPTIONS,
		DEFAULT_BACKENDS_FILTERS,
		applyBackendsFilters,
		backendEngineCounts,
		backendEngines,
		backendsFilterActiveCount,
		backendsFilterChips,
		clearAllBackendsFilters,
		clearBackendsFilterChip,
		type BackendSortBy,
		type BackendsFilters
	} from './backendsFilters';
	import BackendOptimizations from './BackendOptimizations.svelte';
	import BackendQuickActions from './BackendQuickActions.svelte';
	import BackendInfrastructureSection from './BackendInfrastructureSection.svelte';
	import BackendModelsSection from './BackendModelsSection.svelte';
	import { backendDetailTabsFor, backendDetailTabHasFooter, isBackendDetailTab, type BackendDetailTabId } from './backendDetailTabs';
	import { readBackendsUrlState, writeBackendsUrlState } from './backendsUrlState';
	import { BACKENDS_LIBRARY_SECTIONS, type BackendLibrarySection } from './backends/backendsLibrarySections';

	type DetailTab = BackendDetailTabId;

	const NATIVE_LOCAL_DRIVER = 'native.local';
	const NATIVE_REMOTE_DRIVER = 'native.remote';

	interface TestConnectionResult {
		success: boolean;
		message?: string;
	}

	type BackendFormData = Partial<Backend> & Record<string, any>;

	let backends = $state<Backend[]>([]);
	let backendsHealth = $state<BackendHealth[]>([]);
	let engines = $state<EngineDescriptor[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let filters = $state<BackendsFilters>({ ...DEFAULT_BACKENDS_FILTERS });
	let selectedBackendId = $state<string | null>(null);
	let showModal = $state(false);
	let showDeleteModal = $state(false);
	let saving = $state(false);
	let testing = $state(false);
	let testResult = $state<TestConnectionResult | null>(null);
	let deleteTarget = $state<Backend | null>(null);
	let detailTab = $state<DetailTab>('overview');
	let togglingBackendId = $state<string | null>(null);
	let urlRestored = $state(false);
	let detailOverflowOpen = $state(false);
	let detailOverflowEl: HTMLDivElement | undefined = $state();

	let backendStats = $state<BackendStats | null>(null);
	let backendStatsLoading = $state(false);
	let backendStatsError = $state<string | null>(null);

	let indexingBackendId = $state<string | null>(null);
	let indexResults = $state<Record<string, IndexModelsResult>>({});
	let indexUnsupported = $state<Record<string, string>>({});
	let indexWarningsOpen = $state<Record<string, boolean>>({});

	let formData = $state<BackendFormData>(emptyFormData());
	let editFormData = $state<BackendFormData>(emptyFormData());
	let editSnapshot = $state(untrack(() => JSON.stringify(editFormData)));
	let editSaving = $state(false);

	function emptyFormData(driver = ''): BackendFormData {
		const descriptor = engines.find((e) => e.driver === driver);
		const data: BackendFormData = {
			name: '',
			engine: descriptor?.engine ?? driver,
			driver,
			enabled: true,
			priority: 1,
			timeout_seconds: 300,
			scheduling_policy: 'fifo',
			scheduling_max_consecutive_same_model: 3
		};
		return applyEngineDefaults(data, driver);
	}

	function applyEngineDefaults(data: BackendFormData, driver: string): BackendFormData {
		for (const field of fieldsFor(driver)) {
			if (data[field.name] !== null && data[field.name] !== undefined) continue;
			if (field.default !== null && field.default !== undefined) {
				data[field.name] = field.default;
			} else if (field.type === 'boolean') {
				data[field.name] = false;
			} else if (field.type === 'number') {
				data[field.name] = 0;
			} else {
				data[field.name] = '';
			}
		}
		return data;
	}

	function fieldsFor(driver: string): EngineField[] {
		return engines.find((e) => e.driver === driver)?.fields ?? [];
	}

	function buildBackendPayload(source: BackendFormData, opts: { includeDriver?: boolean } = {}): Record<string, unknown> {
		const payload: Record<string, unknown> = {
			name: source.name,
			engine: source.engine,
			enabled: source.enabled,
			priority: source.priority,
			timeout_seconds: source.timeout_seconds,
			scheduling_policy: source.scheduling_policy,
			scheduling_max_consecutive_same_model: source.scheduling_max_consecutive_same_model
		};
		if (opts.includeDriver) payload.driver = source.driver;
		for (const field of fieldsFor(source.driver ?? '')) {
			const value = source[field.name];
			if (field.required || (value !== undefined && value !== null && value !== '')) {
				payload[field.name] = value;
			}
		}
		return payload;
	}

	function defaultCreatableDriver(): string {
		return creatableEngines[0]?.driver ?? '';
	}

	function isBlank(value: unknown): boolean {
		return value === undefined || value === null || value === '';
	}

	function capitalize(value: string): string {
		return value.length ? value.charAt(0).toUpperCase() + value.slice(1) : value;
	}

	const section: BackendLibrarySection = 'all';

	const creatableEngines = $derived(
		engines
			.filter((e) => e.creatable)
			.sort((a, b) => (a.driver === NATIVE_REMOTE_DRIVER ? 1 : b.driver === NATIVE_REMOTE_DRIVER ? -1 : 0))
	);
	const activeEngineFields = $derived(engines.length ? fieldsFor(formData.driver ?? '') : []);
	const activeEditEngineFields = $derived(engines.length ? fieldsFor(editFormData.driver ?? '') : []);
	const canCreateBackend = $derived(
		!saving &&
			!isBlank(formData.name) &&
			activeEngineFields.every((field) => !field.required || !isBlank(formData[field.name]))
	);
	const editDirty = $derived(JSON.stringify(editFormData) !== editSnapshot);

	const filteredBackends = $derived(applyBackendsFilters(backends, filters, formatEngineName));
	const filterChips = $derived(backendsFilterChips(filters));
	const activeFilterCount = $derived(backendsFilterActiveCount(filters));
	const engineList = $derived(backendEngines(backends));
	const engineCounts = $derived(backendEngineCounts(backends));
	const activeBackend = $derived(backends.find((b) => b.id === selectedBackendId) ?? null);
	const activeHealth = $derived(activeBackend ? backendsHealth.find((h) => h.backend_id === activeBackend.id) : undefined);
	const detailOpen = $derived(selectedBackendId !== null);
	const backendDetailTabs = $derived(backendDetailTabsFor(activeBackend?.driver ?? ''));
	const showFooter = $derived(backendDetailTabHasFooter(detailTab));

	const headerChips = $derived.by((): DetailHeaderChip[] => {
		if (!activeBackend) return [];
		const chips: DetailHeaderChip[] = [{ key: 'engine', label: formatEngineName(activeBackend.engine), tone: 'signal' }];
		const healthTone: DetailHeaderChipTone = activeHealth ? getHealthVariant(activeHealth.health.status) : 'neutral';
		chips.push({ key: 'health', label: activeHealth ? capitalize(activeHealth.health.status) : 'Health unknown', tone: healthTone });
		if (activeBackend.is_default) chips.push({ key: 'default', label: 'Default', tone: 'signal' });
		if (!activeBackend.configured) chips.push({ key: 'not-configured', label: 'Not configured', tone: 'warning' });
		return chips;
	});

	$effect(() => {
		if (activeBackend && !isBackendDetailTab(activeBackend.driver, detailTab)) {
			detailTab = 'overview';
		}
	});

	$effect(() => {
		if (detailTab === 'stats' && activeBackend) {
			loadBackendStats(activeBackend.id);
		}
	});

	$effect(() => {
		if (!urlRestored) return;
		const nextUrl = writeBackendsUrlState($page.url, {
			backendId: selectedBackendId,
			view: selectedBackendId ? detailTab : null
		});
		if (nextUrl.search !== $page.url.search) {
			void goto(nextUrl, { replaceState: true, keepFocus: true, noScroll: true });
		}
	});

	onMount(async () => {
		await loadEngines();
		await loadBackends();
		await restoreFromUrl();
		urlRestored = true;
		await loadBackendsHealth();
	});

	async function restoreFromUrl() {
		const { backendId, view } = readBackendsUrlState($page.url.searchParams);
		if (!backendId) return;
		const backend = backends.find((b) => b.id === backendId);
		if (!backend) return;
		await selectBackend(backend.id);
		if (view && isBackendDetailTab(backend.driver, view)) {
			detailTab = view as DetailTab;
		}
	}

	const unsubscribeComputeStatus = adminWebSocket.onComputeStatus(() => {
		loadBackends();
		loadBackendsHealth();
	});
	onDestroy(unsubscribeComputeStatus);

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as Element | null;
		if (target?.closest('[role="dialog"], [role="alertdialog"], [aria-label="Close modal"]')) return;
		if (detailOverflowOpen && detailOverflowEl && !detailOverflowEl.contains(event.target as Node)) {
			detailOverflowOpen = false;
		}
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') detailOverflowOpen = false;
	}

	async function loadEngines() {
		try {
			const response = await getBackendEngines();
			if (!response.success || !Array.isArray(response.data)) return;
			const received = response.data;
			const valid = received.filter(
				(e: unknown): e is EngineDescriptor =>
					!!e &&
					typeof e === 'object' &&
					typeof (e as EngineDescriptor).engine === 'string' &&
					typeof (e as EngineDescriptor).driver === 'string'
			);
			if (valid.length !== received.length) {
				logger.error('Unexpected /api/backends/engines payload:', received);
				loadError = 'The API returned an outdated engine list. Restart the API server.';
				engines = [];
				return;
			}
			engines = valid;
		} catch (e: unknown) {
			logger.warn('Failed to load backend engines:', getErrorMessage(e));
		}
	}

	async function loadBackends() {
		loading = true;
		loadError = null;
		try {
			const response = await getBackends();
			if (response.success) {
				backends = response.data ?? [];
			} else {
				loadError = response.message || 'Failed to load backends';
			}
		} catch (e: unknown) {
			loadError = getApiErrorMessage(e, 'Failed to load backends');
		} finally {
			loading = false;
		}
	}

	async function loadBackendsHealth() {
		try {
			const response = await getAllBackendsHealth();
			if (response.success) {
				backendsHealth = response.data ?? [];
			}
		} catch (e) {
			logger.error('Failed to load backends health:', e);
		}
	}

	function openCreateModal() {
		formData = emptyFormData(defaultCreatableDriver());
		showModal = true;
	}

	function onDriverChange(driver: string) {
		formData = emptyFormData(driver);
	}

	function closeModal() {
		showModal = false;
		formData = emptyFormData();
	}

	async function saveBackend() {
		if (!canCreateBackend) return;
		saving = true;
		try {
			const backendData = buildBackendPayload(formData, { includeDriver: true });
			const response = await createBackend(backendData);
			if (response.success) {
				await loadBackends();
				await loadBackendsHealth();
				closeModal();
				const created = response.data;
				if (created?.id) selectBackend(created.id);
			} else {
				toasts.error(response.message || 'Failed to save backend');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to save backend'));
		} finally {
			saving = false;
		}
	}

	async function handleInfrastructureProvisioned() {
		await loadBackends();
		await loadBackendsHealth();
		if (selectedBackendId) {
			loadEditForm(backends.find((b) => b.id === selectedBackendId) ?? null);
		}
	}

	async function handleInfrastructureTerminated() {
		await loadBackends();
		await loadBackendsHealth();
		if (selectedBackendId) {
			loadEditForm(backends.find((b) => b.id === selectedBackendId) ?? null);
		}
	}

	async function selectBackend(id: string) {
		if (
			editDirty &&
			!(await confirmDialog({
				title: 'Discard unsaved changes',
				message: 'Discard unsaved changes to this backend?',
				variant: 'warning'
			}))
		)
			return;
		selectedBackendId = id;
		detailTab = 'overview';
		loadEditForm(backends.find((b) => b.id === id) ?? null);
	}

	async function backToList() {
		if (
			editDirty &&
			!(await confirmDialog({
				title: 'Discard unsaved changes',
				message: 'Discard unsaved changes to this backend?',
				variant: 'warning'
			}))
		)
			return;
		selectedBackendId = null;
		detailTab = 'overview';
		loadEditForm(null);
	}

	function toggleEngineFilter(engine: string) {
		filters = { ...filters, engine: filters.engine === engine ? '' : engine };
		selectedBackendId = null;
	}

	async function loadBackendStats(backendId: string) {
		backendStatsLoading = true;
		backendStatsError = null;
		try {
			const response = await getBackendStats(backendId);
			if (response.success && response.data) {
				backendStats = response.data;
			} else {
				backendStatsError = response.message || 'Failed to load backend stats';
			}
		} catch (e: unknown) {
			backendStatsError = getApiErrorMessage(e, 'Failed to load backend stats');
		} finally {
			backendStatsLoading = false;
		}
	}

	function loadEditForm(backend: Backend | null) {
		editFormData = backend
			? applyEngineDefaults({ ...backend }, backend.driver ?? backend.engine ?? '')
			: emptyFormData();
		editSnapshot = JSON.stringify(editFormData);
		testResult = null;
		backendStats = null;
	}

	function discardEditForm() {
		loadEditForm(activeBackend);
	}

	async function saveEditForm() {
		if (!activeBackend) return;
		editSaving = true;
		try {
			const backendData = buildBackendPayload(editFormData);
			backendData.id = activeBackend.id;
			const response = await updateBackend(activeBackend.id, backendData);
			if (response.success) {
				toasts.success(`${editFormData.name || activeBackend.name} updated`);
				await loadBackends();
				await loadBackendsHealth();
				loadEditForm(backends.find((b) => b.id === activeBackend!.id) ?? null);
			} else {
				toasts.error(response.message || 'Failed to save backend');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to save backend'));
		} finally {
			editSaving = false;
		}
	}

	function openDeleteModal(backend: Backend) {
		detailOverflowOpen = false;
		deleteTarget = backend;
		showDeleteModal = true;
	}

	function closeDeleteModal() {
		showDeleteModal = false;
		deleteTarget = null;
	}

	async function deleteBackend() {
		if (!deleteTarget) return;
		try {
			const response = await deleteBackendRequest(deleteTarget.id);
			if (response.success) {
				const wasSelected = selectedBackendId === deleteTarget.id;
				await loadBackends();
				await loadBackendsHealth();
				closeDeleteModal();
				if (wasSelected) {
					selectedBackendId = null;
					loadEditForm(null);
				}
			} else {
				toasts.error(response.message || 'Failed to delete backend');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to delete backend'));
		}
	}

	async function makeDefault(backend: Backend) {
		try {
			const response = await setDefaultBackend(backend.id);
			if (response.success) {
				await loadBackends();
			} else {
				toasts.error(response.message || 'Failed to set default backend');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to set default backend'));
		}
	}

	async function toggleEnabled(backend: Backend) {
		togglingBackendId = backend.id;
		const backendData = buildBackendPayload({ ...backend, enabled: !backend.enabled });
		backendData.id = backend.id;
		try {
			const response = await updateBackend(backend.id, backendData);
			if (response.success) {
				await loadBackends();
				await loadBackendsHealth();
				if (selectedBackendId === backend.id) {
					loadEditForm(backends.find((b) => b.id === backend.id) ?? null);
				}
			} else {
				toasts.error(response.message || 'Failed to update backend');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to update backend'));
		} finally {
			togglingBackendId = null;
		}
	}

	async function testConnection(backendId: string) {
		testing = true;
		testResult = null;
		try {
			testResult = await testBackend(backendId);
		} catch (e: unknown) {
			testResult = {
				success: false,
				message: getApiErrorMessage(e, 'Connection test failed')
			};
		} finally {
			testing = false;
		}
	}

	async function indexModels(backend: Backend) {
		indexingBackendId = backend.id;
		if (indexUnsupported[backend.id]) {
			const { [backend.id]: _removed, ...rest } = indexUnsupported;
			indexUnsupported = rest;
		}
		try {
			const response = await indexBackendModels(backend.id);
			if (response.success && response.data) {
				indexResults = { ...indexResults, [backend.id]: response.data };
				const r = response.data;
				const warnings =
					r.size_conflicts.length + r.digest_conflicts.length + r.duplicates.length + r.ambiguous.length;
				toasts.success(
					`Indexed ${r.listed} models on "${backend.name}" — ${r.created} new, ${r.matched} matched, ${r.removed} removed` +
						(warnings > 0 ? ` (${warnings} warning${warnings === 1 ? '' : 's'})` : '')
				);
				if (selectedBackendId === backend.id && detailTab === 'stats') {
					loadBackendStats(backend.id);
				}
			} else {
				toasts.error(response.message || `Failed to index models for "${backend.name}"`);
			}
		} catch (e: unknown) {
			const detail = isAxiosError<{ message?: string; detail?: { error?: string; message?: string } }>(e)
				? e.response?.data?.detail
				: undefined;
			if (detail?.error === 'model_listing_not_supported') {
				indexUnsupported = {
					...indexUnsupported,
					[backend.id]:
						detail.message ||
						`"${backend.name}" cannot report which models it has, so generations can't be routed to it by availability.`
				};
			} else {
				toasts.error(getApiErrorMessage(e, `Failed to index models for "${backend.name}"`));
			}
		} finally {
			indexingBackendId = null;
		}
	}

	function toggleIndexWarnings(backendId: string) {
		indexWarningsOpen = { ...indexWarningsOpen, [backendId]: !indexWarningsOpen[backendId] };
	}

	function getHealthVariant(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
		if (status === 'healthy' || status === 'online' || status === 'available') return 'success';
		if (status === 'degraded') return 'warning';
		if (status === 'offline' || status === 'error') return 'danger';
		return 'neutral';
	}

	function healthStatusCellTone(status: string | undefined): 'success' | 'danger' | 'warning' | 'muted' {
		if (!status) return 'muted';
		const variant = getHealthVariant(status);
		return variant === 'neutral' ? 'muted' : variant;
	}

	function formatEngineName(engine: string): string {
		const label = engines.find((e) => e.engine === engine)?.label;
		return label || engine.charAt(0).toUpperCase() + engine.slice(1).replace(/_/g, ' ');
	}

	function formatDriverLabel(driver: string): string {
		const label = engines.find((e) => e.driver === driver)?.label;
		return label || formatEngineName(driver);
	}

	function hostPortLabel(backend: Backend): string {
		return backend.host && backend.port ? `${backend.host}:${backend.port}` : '—';
	}

	function healthLabelFor(backend: Backend): string {
		const health = backendsHealth.find((h) => h.backend_id === backend.id);
		return health ? capitalize(health.health.status) : 'Unknown';
	}

	function healthStatusFor(backend: Backend): string | undefined {
		return backendsHealth.find((h) => h.backend_id === backend.id)?.health.status;
	}

	const columns: DataTableColumn<Backend>[] = $derived.by(() => [
		{ key: 'name', label: 'Name', width: 'minmax(160px,2fr)', cell: nameCell },
		{ key: 'engine', label: 'Engine', width: '110px', accessor: (b) => formatEngineName(b.engine) },
		{ key: 'driver', label: 'Driver', width: '160px', accessor: (b) => formatDriverLabel(b.driver) },
		{ key: 'hostPort', label: 'Host:port', width: '150px', mono: true, priority: 1, accessor: hostPortLabel },
		{ key: 'health', label: 'Health', width: '110px', cell: healthCell },
		{ key: 'enabled', label: 'Enabled', width: '100px', priority: 1, cell: enabledCell }
	]);
</script>

{#snippet workerUrlEditHint()}
	<Alert variant="info" density="compact">
		A Remote Native backend needs a running worker. Paste the URL of a worker you started
		yourself, or provision one from this backend's Infrastructure tab — its URL and token are
		filled in for you automatically. Models must live under POTIONUI_WORKER_MODEL_DIR on the
		worker (default /models) — see the Models tab for exact per-file paths.
	</Alert>
{/snippet}
{#snippet workerUrlCreateHint()}
	<Alert variant="info" density="compact">
		A Remote Native backend needs a running worker. Paste the URL of a worker you started
		yourself, or leave this blank — after creating, open its Infrastructure tab to provision one
		and its URL and token are filled in for you automatically. Models must live under
		POTIONUI_WORKER_MODEL_DIR on the worker (default /models) — see the Models tab for exact
		per-file paths.
	</Alert>
{/snippet}

{#snippet nameCell(backend: Backend)}
	<div class="flex items-center gap-2 min-w-0">
		<span class="truncate">{backend.name}</span>
		{#if backend.is_default}<Badge variant="signal" size="sm">Default</Badge>{/if}
	</div>
{/snippet}

{#snippet healthCell(backend: Backend)}
	<StatusCell tone={healthStatusCellTone(healthStatusFor(backend))} label={healthLabelFor(backend)} />
{/snippet}

{#snippet enabledCell(backend: Backend)}
	<StatusCell tone={backend.enabled ? 'success' : 'muted'} label={backend.enabled ? 'Enabled' : 'Disabled'} />
{/snippet}

{#snippet rowCard(backend: Backend)}
	<div class="truncate text-sm font-semibold text-fg">{backend.name}</div>
	<div class="mt-1 flex items-center gap-2">
		<StatusCell tone={healthStatusCellTone(healthStatusFor(backend))} label={healthLabelFor(backend)} />
		<span class="font-mono text-2xs text-fg-subtle">{formatEngineName(backend.engine)}</span>
	</div>
{/snippet}

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<LibraryShell
	title="Backends"
	persistKey="admin-backends-library"
	heightClass="h-full"
	sections={BACKENDS_LIBRARY_SECTIONS}
	{section}
	onSelectSection={() => {}}
	sectionCounts={{ all: backends.length }}
	count={filteredBackends.length}
	{detailOpen}
	filterChips={filterChips}
	onRemoveChip={(key) => (filters = clearBackendsFilterChip(filters, key))}
	onClearFilters={() => (filters = clearAllBackendsFilters(filters))}
	loadedCount={filteredBackends.length}
	total={backends.length}
>
	{#snippet sidebarTree()}
		{#if engineList.length}
			<div class="border-t border-line">
				<div class="px-3 pb-1 pt-2 font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">By engine</div>
				<div class="space-y-0.5 p-2 pt-0">
					{#each engineList as engine (engine)}
						<PaneRow
							title={engine}
							count={engineCounts[engine] ?? 0}
							selected={filters.engine === engine}
							onclick={() => toggleEngineFilter(engine)}
						/>
					{/each}
				</div>
			</div>
		{/if}
	{/snippet}

	{#snippet toolbar()}
		<LibraryFilterBar
			q={filters.q}
			onQueryChange={(value) => (filters = { ...filters, q: value })}
			searchPlaceholder="Search by name or engine…"
			sortBy={filters.sortBy}
			sortOptions={BACKENDS_SORT_OPTIONS}
			onSortChange={(value) => (filters = { ...filters, sortBy: value as BackendSortBy })}
			filterCount={activeFilterCount}
		/>
	{/snippet}

	{#snippet primary()}
		<Button variant="primary" size="sm" icon="plus" onclick={openCreateModal}>Add backend</Button>
	{/snippet}

	{#if detailOpen}
		{#if !activeBackend}
			<div class="flex h-full items-center justify-center">
				{#if loading}
					<Spinner size="lg" />
				{:else}
					<EmptyState title="Backend not found" description="This backend may have been removed." icon="server" compact>
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={backToList}>Back to backends</Button>{/snippet}
					</EmptyState>
				{/if}
			</div>
		{:else}
			<div class="flex h-full flex-col">
				<DetailHeader
					title={activeBackend.name}
					icon={adminSectionIcon('backends')}
					backLabel="Backends"
					onBack={backToList}
					chipItems={headerChips}
				>
					{#snippet subtitle()}{hostPortLabel(activeBackend)}{/snippet}
					{#snippet enabledSwitch()}
						<label class="flex items-center gap-1.5">
							<span class="text-2xs text-fg-subtle">Enabled</span>
							<Switch
								checked={activeBackend.enabled}
								busy={togglingBackendId === activeBackend.id}
								size="lg"
								onchange={() => activeBackend && toggleEnabled(activeBackend)}
								label="Backend enabled"
							/>
						</label>
					{/snippet}
					{#snippet actions()}
						<Tooltip text={testing ? 'Testing…' : 'Test connection'}>
							<IconButton icon="check" label="Test connection" disabled={testing} onclick={() => activeBackend && testConnection(activeBackend.id)} />
						</Tooltip>
						<Tooltip text={indexingBackendId === activeBackend.id ? 'Indexing…' : 'Index models'}>
							<IconButton
								icon="refresh"
								label="Index models"
								disabled={indexingBackendId !== null && indexingBackendId !== activeBackend.id}
								onclick={() => activeBackend && indexModels(activeBackend)}
							/>
						</Tooltip>
						<BackendQuickActions
							actions={activeBackend.quick_actions ?? []}
							backendName={activeBackend.name}
							onDone={() => {
								loadBackends();
								loadBackendsHealth();
							}}
						/>
						{#if !activeBackend.is_default}
							<Button variant="primary" size="sm" icon="star" onclick={() => activeBackend && makeDefault(activeBackend)}>Make default</Button>
						{/if}
						{#if activeBackend.driver !== NATIVE_LOCAL_DRIVER}
							<div class="relative" bind:this={detailOverflowEl}>
								<Tooltip text="More actions">
									<IconButton icon="more" label="More actions" ariaExpanded={detailOverflowOpen} active={detailOverflowOpen} onclick={() => (detailOverflowOpen = !detailOverflowOpen)} />
								</Tooltip>
								{#if detailOverflowOpen}
									<div class="absolute right-0 top-[calc(100%+6px)] z-40 min-w-[200px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating" role="menu">
										<button
											type="button"
											role="menuitem"
											class="w-full px-3 py-2 text-left text-xs flex items-center gap-2 text-danger hover:bg-danger/10"
											onclick={() => activeBackend && openDeleteModal(activeBackend)}
										>
											<Icon name="trash" className="w-3.5 h-3.5" />
											Delete backend
										</button>
									</div>
								{/if}
							</div>
						{/if}
					{/snippet}
				</DetailHeader>

				<DetailTabs tabs={backendDetailTabs} active={detailTab} onSelect={(id) => (detailTab = id as DetailTab)} ariaLabel="Backend details" />

				<div class="flex-1 min-h-0 flex flex-col">
					{#if detailTab === 'overview'}
						<DetailBody>
							<DetailLayout>
								{#snippet lead()}
									{#if testResult}
										<Alert variant={testResult.success ? 'success' : 'danger'} density="compact" title={testResult.success ? 'Connection successful' : 'Connection failed'}>
											{#if testResult.message}{testResult.message}{/if}
										</Alert>
									{/if}
									{#if indexUnsupported[activeBackend.id]}
										<Alert variant="warning" density="compact">
											{indexUnsupported[activeBackend.id]}
										</Alert>
									{/if}
								{/snippet}
								{#snippet main()}
									{#if indexResults[activeBackend.id] && !indexUnsupported[activeBackend.id]}
										{@const result = indexResults[activeBackend.id]}
										{@const warningCount =
											result.size_conflicts.length +
											result.digest_conflicts.length +
											result.duplicates.length +
											result.ambiguous.length}
										{@const hasDigestConflicts = result.digest_conflicts.length > 0}
										<DetailSection label="Last index run">
											<p class="text-xs font-mono tabular-nums text-fg-muted">
												Indexed {result.listed} models — {result.created} new, {result.matched} matched,
												{result.removed} removed
											</p>
											{#if warningCount > 0}
												<button
													type="button"
													class="mt-2 flex items-center gap-1.5 text-xs font-mono uppercase tracking-[0.05em] {hasDigestConflicts
														? 'text-danger'
														: 'text-warning'} hover:underline"
													onclick={() => toggleIndexWarnings(activeBackend.id)}
												>
													<Badge variant={hasDigestConflicts ? 'danger' : 'warning'} size="sm" dot>
														{warningCount} warning{warningCount === 1 ? '' : 's'}
													</Badge>
													<span>{indexWarningsOpen[activeBackend.id] ? 'Hide' : 'Show'}</span>
												</button>
												{#if indexWarningsOpen[activeBackend.id]}
													<ul class="mt-2 space-y-1.5 text-xs text-fg-subtle leading-relaxed">
														{#each result.digest_conflicts as conflict}
															<li class="font-mono text-danger">
																Digest conflict: <span class="text-fg-muted">{conflict.filename}</span>
																({conflict.model_type}) — this backend's copy does not match the expected
																content and has been excluded from routing. Re-sync or replace the file,
																then re-index.
															</li>
														{/each}
														{#each result.size_conflicts as conflict}
															<li class="font-mono">
																Size conflict: <span class="text-fg-muted">{conflict.filename}</span>
																({conflict.model_type}) — known {conflict.known_size} bytes, this backend
																reports {conflict.reported_size} bytes. Likely a different (e.g. quantised)
																copy.
															</li>
														{/each}
														{#each result.duplicates as dup}
															<li class="font-mono">
																Duplicate content: <span class="text-fg-muted">{dup.ref}</span>
																({dup.model_type}) has the same sha256 as
																<span class="text-fg-muted">{dup.existing_filename}</span>
																({dup.existing_model_type}{dup.existing_file_path
																	? `, ${dup.existing_file_path}`
																	: ''}) and was skipped. Remove one copy.
															</li>
														{/each}
														{#each result.ambiguous as note}
															<li class="font-mono">{note}</li>
														{/each}
													</ul>
												{/if}
											{/if}
										</DetailSection>
									{/if}

									<BackendForm
										bind:draft={editFormData}
										mode="edit"
										layout="panel"
										idPrefix="edit-backend"
										engineMutable={false}
										engineLabel={formatDriverLabel(activeBackend.driver)}
										fieldDescriptors={activeEditEngineFields}
										enabledPlacement="none"
										fieldHints={activeBackend.driver === NATIVE_REMOTE_DRIVER
											? { base_url: workerUrlEditHint }
											: {}}
									/>
								{/snippet}
								{#snippet aside()}
									<DetailSection label="Metadata">
										<KVGrid>
											<KVItem label="Engine" mono>{formatEngineName(activeBackend.engine)}</KVItem>
											<KVItem label="Driver" mono>{formatDriverLabel(activeBackend.driver)}</KVItem>
											<KVItem label="Models indexed" mono>
												{backendStats && backendStats.backend_id === activeBackend.id
													? backendStats.indexed_models
													: (indexResults[activeBackend.id]?.listed ?? '—')}
											</KVItem>
										</KVGrid>
									</DetailSection>
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if detailTab === 'infrastructure' && activeBackend.driver === NATIVE_REMOTE_DRIVER}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									{#key activeBackend.id}
										<BackendInfrastructureSection
											backendId={activeBackend.id}
											backendDriver={activeBackend.driver}
											configured={activeBackend.configured}
											backendEnabled={activeBackend.enabled}
											onStopped={() => {
												loadBackends();
												loadBackendsHealth();
											}}
											onProvisioned={handleInfrastructureProvisioned}
											onTerminated={handleInfrastructureTerminated}
											onEnableBackend={() => activeBackend && toggleEnabled(activeBackend)}
										/>
									{/key}
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if detailTab === 'models' && activeBackend.driver === NATIVE_REMOTE_DRIVER}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									{#if !activeBackend.configured}
										<EmptyState
											icon="cube"
											title="Worker not connected"
											description="Set a Worker URL in Overview or provision one in Infrastructure to list this worker's models. Models are looked up under POTIONUI_WORKER_MODEL_DIR on the worker (default /models), one folder per model type — the same layout as this host's models folder."
											compact
										>
											{#snippet actions()}
												<Button variant="secondary" size="sm" icon="server" onclick={() => (detailTab = 'infrastructure')}>
													Go to Infrastructure
												</Button>
											{/snippet}
										</EmptyState>
									{:else}
										{#key activeBackend.id}
											<BackendModelsSection
												backendId={activeBackend.id}
												onOpenInfrastructure={() => (detailTab = 'infrastructure')}
											/>
										{/key}
									{/if}
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if detailTab === 'optimizations' && activeBackend.driver === NATIVE_LOCAL_DRIVER}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									{#key activeBackend.id}
										<BackendOptimizations backendId={activeBackend.id} />
									{/key}
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if detailTab === 'stats'}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									{#if backendStatsLoading}
										<div class="flex items-center justify-center py-12">
											<Spinner size="lg" />
										</div>
									{:else if backendStatsError}
										<EmptyState title="Stats unavailable" description={backendStatsError} icon="warning" compact>
											{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={() => activeBackend && loadBackendStats(activeBackend.id)}>Try again</Button>{/snippet}
										</EmptyState>
									{:else if backendStats}
										<DetailSection label="Stats">
											<KVGrid>
												<KVItem label="Indexed models" mono>{backendStats.indexed_models}</KVItem>
												<KVItem label="Total size" mono>{backendStats.total_size_gb.toFixed(1)} GB</KVItem>
												<KVItem label="Last indexed" mono>
													{backendStats.last_indexed_at ? timeAgo(backendStats.last_indexed_at) : 'Never'}
												</KVItem>
											</KVGrid>
										</DetailSection>
										{#if backendStats.indexed_models === 0}
											<p class="text-sm text-fg-muted">
												This backend hasn't been indexed yet — these numbers come from its own
												index, not a live scan. Use "Index models" above to populate them.
											</p>
										{/if}
									{/if}
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{/if}
				</div>

				{#if showFooter}
					<DetailFooter
						dirtyCount={editDirty ? 1 : 0}
						mode="edit"
						saving={editSaving}
						canSave={true}
						onSave={saveEditForm}
						onDiscard={discardEditForm}
					/>
				{/if}
			</div>
		{/if}
	{:else}
		<div class="flex flex-col gap-3 p-4">
			{#if loadError}
				<Alert variant="danger" icon title="Error">
					{loadError}
				</Alert>
			{/if}

			<DataTable
				{columns}
				rows={filteredBackends}
				getRowId={(b) => b.id}
				onRowClick={(b) => selectBackend(b.id)}
				loading={loading && backends.length === 0}
				isFiltered={filteredBackends.length !== backends.length}
				card={rowCard}
			>
				{#snippet emptyState()}
					<EmptyState
						icon="server"
						title="No backends configured yet"
						description="A backend tells PotionUI where to run generations for an engine — for example, the built-in native engine or a ComfyUI server. Add one to start generating."
					>
						{#snippet actions()}
							<Button variant="primary" icon="plus" onclick={openCreateModal}>Add backend</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState icon="search" title="No backends match" description="Try a different name or engine." compact>
						{#snippet actions()}
							<Button variant="ghost" size="sm" onclick={() => (filters = clearAllBackendsFilters(filters))}>Clear filters</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
			</DataTable>
		</div>
	{/if}
</LibraryShell>

<BaseModal isOpen={showModal} title="Add Backend" sizeClass="md:max-w-2xl md:w-full" on:close={closeModal}>
	<div class="px-6 py-4">
		<BackendForm
			bind:draft={formData}
			mode="create"
			layout="plain"
			idPrefix="create-backend"
			engineMutable={true}
			{creatableEngines}
			{onDriverChange}
			fieldDescriptors={activeEngineFields}
			enabledPlacement="inline"
			fieldHints={formData.driver === NATIVE_REMOTE_DRIVER ? { base_url: workerUrlCreateHint } : {}}
		/>
	</div>

	<svelte:fragment slot="footer">
		<div class="px-6 py-4 flex gap-3">
			<Button variant="primary" class="flex-1" loading={saving} disabled={!canCreateBackend} onclick={saveBackend}>
				{saving ? 'Creating…' : 'Create Backend'}
			</Button>
			<Button variant="secondary" onclick={closeModal}>Cancel</Button>
		</div>
	</svelte:fragment>
</BaseModal>

<ConfirmModal
	isOpen={showDeleteModal && !!deleteTarget}
	title="Delete Backend"
	message={deleteTarget
		? `Are you sure you want to delete the backend "${deleteTarget.name}"? This action cannot be undone.`
		: ''}
	variant="danger"
	on:confirm={deleteBackend}
	on:cancel={closeDeleteModal}
/>
