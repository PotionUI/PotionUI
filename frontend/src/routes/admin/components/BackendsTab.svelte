<script lang="ts">
	import { logger, getErrorMessage, getApiErrorMessage } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { isAxiosError } from 'axios';
	import type { ComputeProvider, EngineDescriptor, EngineField, IndexModelsResult, BackendStats } from '$lib/services/admin-api';
	import {
		indexBackendModels,
		getBackendStats,
		getBackendEngines,
		getBackends,
		getAllBackendsHealth,
		getComputeProviders,
		createBackend,
		updateBackend,
		deleteBackend as deleteBackendRequest,
		setDefaultBackend,
		testBackend
	} from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { Button, Badge, Spinner, EmptyState, Input, Switch, Alert, SegmentedControl } from '$lib/components/ui';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import BackendForm from './BackendForm.svelte';
	import { MasterDetailLayout } from '$lib/components/master-detail';
	import { Pane, PaneRow, PaneGroupHeader } from '$lib/components/pane';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import type { Backend, BackendHealth } from '$lib/services/admin-api';
	import BackendOptimizations from './BackendOptimizations.svelte';
	import BackendQuickActions from './BackendQuickActions.svelte';
	import RemoteWorkerProvisionForm from './RemoteWorkerProvisionForm.svelte';
	import BackendInfrastructureSection from './BackendInfrastructureSection.svelte';

	type DetailTab = 'overview' | 'optimizations' | 'stats';
	type CreateSource = 'connect' | 'provision';

	// The two built-in `native` drivers (see backend_config.py). Both are core,
	// not plugin-contributed, so - like the pre-existing `engine === 'native'`
	// checks below - naming them here isn't the kind of plugin-name hardcoding
	// CLAUDE.md forbids. `native.local` is the always-present, in-process,
	// singleton driver; `native.remote` is the user-creatable Remote Native
	// worker driver. A capability gated on running in-process (Optimizations,
	// delete-protection) must check the DRIVER, not the engine - both drivers
	// report engine="native".
	const NATIVE_LOCAL_DRIVER = 'native.local';
	const NATIVE_REMOTE_DRIVER = 'native.remote';

	interface TestConnectionResult {
		success: boolean;
		message?: string;
	}

	// State
	let backends: Backend[] = [];
	let backendsHealth: BackendHealth[] = [];
	let engines: EngineDescriptor[] = [];
	// Compute providers, for the create modal's Connect/Provision toggle - a
	// provider always provisions a native.remote backend (see
	// src.features.provisioning.operations.provision_compute), so this is the
	// only extra state the create flow needs beyond `engines`.
	let providers: ComputeProvider[] = [];
	let createSource: CreateSource = 'connect';
	let loading = true;
	let error: string | null = null;
	let searchQuery = '';
	let selectedBackendId: string | null = null;
	let showModal = false;
	let showDeleteModal = false;
	let saving = false;
	let testing = false;
	let testResult: TestConnectionResult | null = null;
	let deleteTarget: Backend | null = null;
	let detailTab: DetailTab = 'overview';
	let togglingBackendId: string | null = null;

	// Stats tab: how many models this backend has reported and how much disk
	// space they total - fetched fresh whenever the tab is opened, so
	// it reflects the latest "Index models" run without a manual refresh.
	let backendStats: BackendStats | null = null;
	let backendStatsLoading = false;
	let backendStatsError: string | null = null;

	// Model indexing, keyed by backend id: which backend is currently indexing, the
	// last result it produced (kept until the next run or a page reload — the server
	// doesn't expose a "last indexed at" field, so this is the only record we have),
	// and any "this backend can't list its models" notice.
	let indexingBackendId: string | null = null;
	let indexResults: Record<string, IndexModelsResult> = {};
	let indexUnsupported: Record<string, string> = {};
	let indexWarningsOpen: Record<string, boolean> = {};

	// Which engines exist, what they're called, and what settings they need is
	// reported by the server (`GET /api/backends/engines`). Nothing here may
	// hardcode an engine name: `comfyui` is contributed by a plugin and may be
	// absent entirely.

	// Form state. Engine-specific keys are not known statically (the engine declares
	// them), so the form model is an open record rather than a strict Backend.
	type BackendFormData = Partial<Backend> & Record<string, any>;

	// Create-modal form (unsaved, new backend only — the modal never edits).
	let formData: BackendFormData = emptyFormData();

	// Detail-pane edit form: a live draft for the selected backend, edited
	// directly in the pane. A separate draft from `formData`, snapshotted on
	// load/save/discard to drive `editDirty` without a heavier dirty-tracking system.
	let editFormData: BackendFormData = emptyFormData();
	let editSnapshot = JSON.stringify(editFormData);
	let editSaving = false;

	// `driver` is passed in rather than derived here: this runs during component
	// init, before any `$:` statement has executed and before onMount has fetched
	// the engine list, so a derived value would still be undefined.
	function emptyFormData(driver = ''): BackendFormData {
		const descriptor = engines.find((e) => e.driver === driver);
		const data: BackendFormData = {
			name: '',
			engine: descriptor?.engine ?? driver,
			driver,
			enabled: true,
			priority: 1,
			timeout_seconds: 300
		};
		return applyEngineDefaults(data, driver);
	}

	/** Seed the driver's own fields with the defaults the server declared for them.
	 * Every declared field ends up DEFINED: a field without a stored value or a
	 * server default gets a type-appropriate empty — `bind:value={undefined}`
	 * crashes at runtime against the Input primitive's fallback default. */
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

	/** Construct a PUT/POST body from a form-shaped source: common fields plus whatever the
	 * driver declares. `driver` is included only on create - it's immutable after that, and
	 * (unlike `engine`) the update route doesn't re-pin it to the stored value, so an update
	 * payload must never carry a driver that could ever be misread as a request to change it. */
	function buildBackendPayload(source: BackendFormData, opts: { includeDriver?: boolean } = {}): Record<string, unknown> {
		const payload: Record<string, unknown> = {
			name: source.name,
			engine: source.engine,
			enabled: source.enabled,
			priority: source.priority,
			timeout_seconds: source.timeout_seconds
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

	/** The driver to preselect when the create modal opens (engines load on mount). */
	function defaultCreatableDriver(): string {
		return creatableEngines[0]?.driver ?? '';
	}

	// Drivers the "create backend" dropdown may offer (native.local is auto-provisioned,
	// never creatable). native.remote sorts last - it's always registered before any
	// plugin engine load, so without this it would otherwise win the default selection
	// over the engine an admin more commonly wants (e.g. a just-enabled comfyui plugin).
	$: creatableEngines = engines
		.filter((e) => e.creatable)
		.sort((a, b) => (a.driver === NATIVE_REMOTE_DRIVER ? 1 : b.driver === NATIVE_REMOTE_DRIVER ? -1 : 0));

	// Fields declared by the driver currently selected in the create form.
	// `engines` is named explicitly so this recomputes once the engine list arrives;
	// Svelte derives dependencies syntactically and would not see it inside fieldsFor().
	$: activeEngineFields = engines.length ? fieldsFor(formData.driver ?? '') : [];

	// Fields declared by the driver of the backend currently being edited in the pane.
	$: activeEditEngineFields = engines.length ? fieldsFor(editFormData.driver ?? '') : [];

	// Whether the create modal offers "Provision new compute" alongside "Connect to
	// existing worker" - only meaningful once native.remote is the selected driver,
	// and only when at least one compute provider plugin is enabled.
	$: showCreateSourceToggle = formData.driver === NATIVE_REMOTE_DRIVER && providers.length > 0;

	// True once the pane's draft diverges from the last loaded/saved snapshot.
	$: editDirty = JSON.stringify(editFormData) !== editSnapshot;

	// Stats
	$: totalBackends = backends.length;
	$: enabledBackends = backends.filter((b) => b.enabled).length;
	$: healthyBackends = backendsHealth.filter(
		(h) => h.health.status === 'healthy' || h.health.status === 'online' || h.health.status === 'available'
	).length;

	// Backends grouped by engine, for a clearly-labeled layout. Search filters
	// cards across the groups; total/enabled/healthy counts above stay based
	// on the full unfiltered list.
	$: filteredBackends = backends.filter((b) => {
		const q = searchQuery.trim().toLowerCase();
		if (!q) return true;
		return b.name?.toLowerCase().includes(q) || formatEngineName(b.engine).toLowerCase().includes(q);
	});
	$: backendsByEngine = groupByEngine(filteredBackends);
	// Derived from the full (unfiltered) list so the detail pane keeps showing
	// the selected backend even while a search hides it from the list.
	$: activeBackend = backends.find((b) => b.id === selectedBackendId) ?? null;
	$: activeHealth = activeBackend
		? backendsHealth.find((h) => h.backend_id === activeBackend!.id)
		: undefined;
	$: activeIsHealthy = !!activeHealth && isHealthy(activeHealth.health.status);

	const dotColorClasses: Record<string, string> = {
		success: 'bg-success-solid',
		warning: 'bg-warning',
		danger: 'bg-danger',
		neutral: 'bg-line-strong'
	};

	function groupByEngine(list: Backend[]): { engine: string; items: Backend[] }[] {
		const groups = new Map<string, Backend[]>();
		for (const b of list) {
			const key = b.engine;
			if (!groups.has(key)) groups.set(key, []);
			groups.get(key)!.push(b);
		}
		return Array.from(groups.entries())
			.map(([engine, items]) => ({ engine, items }))
			.sort((a, b) => a.engine.localeCompare(b.engine));
	}

	// Load backends on mount
	onMount(async () => {
		await loadEngines();
		await loadBackends();
		await loadBackendsHealth();
		await loadProviders();
	});

	// Load supported backend engines
	async function loadEngines() {
		try {
			const response = await getBackendEngines();
			if (!response.success || !Array.isArray(response.data)) return;

			// `/engines` returns one descriptor per DRIVER (not deduped by engine -
			// see EngineDescriptor). An older server returned bare name strings or
			// omitted `driver`; rather than silently degrading (which would key the
			// engine <select> on `undefined` and crash), reject the payload and say why.
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
				error = 'The API returned an outdated engine list. Restart the API server.';
				engines = [];
				return;
			}

			engines = valid;
		} catch (e: unknown) {
			logger.warn('Failed to load backend engines:', getErrorMessage(e));
		}
	}

	// Load compute providers, for the create modal's Provision toggle. Best-effort -
	// no provider plugin enabled just means that toggle never appears.
	async function loadProviders() {
		try {
			const response = await getComputeProviders();
			if (response.success && response.data) providers = response.data.providers;
		} catch (e: unknown) {
			logger.warn('Failed to load compute providers:', getErrorMessage(e));
		}
	}

	// Load backends
	async function loadBackends() {
		loading = true;
		error = null;
		try {
			const response = await getBackends();
			if (response.success) {
				backends = response.data ?? [];
			} else {
				error = response.message || 'Failed to load backends';
			}
		} catch (e: unknown) {
			error = getApiErrorMessage(e, 'Failed to load backends');
		} finally {
			loading = false;
		}
	}

	// Load backends health
	async function loadBackendsHealth() {
		try {
			const response = await getAllBackendsHealth();
			if (response.success) {
				backendsHealth = response.data ?? [];
				// Force re-render of backends to update health status
				backends = [...backends];
			}
		} catch (e) {
			logger.error('Failed to load backends health:', e);
		}
	}

	// Open create modal
	function openCreateModal() {
		formData = emptyFormData(defaultCreatableDriver());
		createSource = 'connect';
		showModal = true;
	}

	/** Reseed driver-declared defaults when the admin picks a different engine/driver. */
	function onDriverChange(driver: string) {
		formData = emptyFormData(driver);
		createSource = 'connect';
	}

	// Close modal
	function closeModal() {
		showModal = false;
		formData = emptyFormData();
		createSource = 'connect';
	}

	// Create a new backend from the modal form ("Connect to existing worker" for
	// native.remote, or any other driver's own form).
	async function saveBackend() {
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
			// The server also refuses driver: "native.local" (400) — surface its message
			// rather than assuming success, since the UI shouldn't have offered this anyway.
			toasts.error(getApiErrorMessage(e, 'Failed to save backend'));
		} finally {
			saving = false;
		}
	}

	// Called by RemoteWorkerProvisionForm once provisioning has created the backend
	// itself - this file never builds that payload, since it's a different endpoint
	// (POST /api/admin/provisioning) with its own provider-defined field shapes.
	async function handleRemoteWorkerCreated(backendId: string) {
		await loadBackends();
		await loadBackendsHealth();
		closeModal();
		selectBackend(backendId);
	}

	// Terminating provisioned infrastructure removes its linked backend row -
	// the currently-selected backend no longer exists once this fires.
	async function handleInfrastructureTerminated() {
		await loadBackends();
		await loadBackendsHealth();
		selectedBackendId = null;
		loadEditForm(null);
	}

	// Select a backend for the detail pane, loading its edit draft. If the
	// current draft has unsaved changes, confirm before discarding them —
	// a lightweight guard rather than a full dirty-tracking system.
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

	// Fetch fresh stats whenever the Stats tab becomes active for a backend.
	$: if (detailTab === 'stats' && activeBackend) {
		loadBackendStats(activeBackend.id);
	}

	function loadEditForm(backend: Backend | null) {
		// A stored backend record doesn't necessarily carry every field its
		// driver declares (fields added later, never-set optionals) — seed the
		// gaps so no bind target is ever undefined.
		editFormData = backend
			? applyEngineDefaults({ ...backend }, backend.driver ?? backend.engine ?? '')
			: emptyFormData();
		editSnapshot = JSON.stringify(editFormData);
		testResult = null;
	}

	function discardEditForm() {
		loadEditForm(activeBackend);
	}

	// Save the detail pane's edit draft.
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

	// Open delete modal
	function openDeleteModal(backend: Backend) {
		deleteTarget = backend;
		showDeleteModal = true;
	}

	// Close delete modal
	function closeDeleteModal() {
		showDeleteModal = false;
		deleteTarget = null;
	}

	// Delete backend
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
			// The server refuses to delete the native backend (400) — surface its message
			// rather than assuming success, since the UI shouldn't have offered this anyway.
			toasts.error(getApiErrorMessage(e, 'Failed to delete backend'));
		}
	}

	// Set backend as default for its engine
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

	// Flip enabled/disabled immediately — a toolbar action, not part of the edit draft.
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

	// Test connection for an existing (already-saved) backend.
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

	// Ask a backend what models it can see and reconcile that against known models.
	// Can take several seconds: native walks the filesystem, ComfyUI does HTTP.
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
				const warnings = r.size_conflicts.length + r.digest_conflicts.length + r.ambiguous.length;
				toasts.success(
					`Indexed ${r.listed} models on "${backend.name}" — ${r.created} new, ${r.matched} matched, ${r.removed} removed` +
						(warnings > 0 ? ` (${warnings} warning${warnings === 1 ? '' : 's'})` : '')
				);
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

	// Get health status badge variant
	function getHealthVariant(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
		if (status === 'healthy' || status === 'online' || status === 'available') return 'success';
		if (status === 'degraded') return 'warning';
		if (status === 'offline' || status === 'error') return 'danger';
		return 'neutral';
	}

	// Display name for an engine, as declared by the server. Used where backends are
	// grouped/searched at engine granularity (a pane group holds every driver of an
	// engine together) - falls back to the first descriptor sharing that engine name
	// if more than one does, which is fine at this granularity.
	function formatEngineName(engine: string): string {
		const label = engines.find((e) => e.engine === engine)?.label;
		return label || engine.charAt(0).toUpperCase() + engine.slice(1).replace(/_/g, ' ');
	}

	// Display name for a specific DRIVER - distinguishes "Native" from "Native
	// (Remote Worker)", which share engine="native" and would collide under
	// formatEngineName. Used wherever a single backend's own identity is shown.
	function formatDriverLabel(driver: string): string {
		const label = engines.find((e) => e.driver === driver)?.label;
		return label || formatEngineName(driver);
	}

	// Check if backend is healthy/available
	function isHealthy(status: string): boolean {
		return status === 'healthy' || status === 'online' || status === 'available';
	}
</script>

<style>
	/* Subtle pulsing glow animation for healthy backends */
	:global(.backend-card-healthy) {
		animation: backend-pulse-glow 3s ease-in-out infinite;
		border-color: rgb(var(--success-solid) / 0.5) !important;
	}

	@keyframes backend-pulse-glow {
		0%, 100% {
			box-shadow: 0 0 2px rgb(var(--success-solid) / 0.2), 0 0 6px rgb(var(--success-solid) / 0.1);
		}
		50% {
			box-shadow: 0 0 4px rgb(var(--success-solid) / 0.3), 0 0 12px rgb(var(--success-solid) / 0.15);
		}
	}

	/* Subtle pulsing glow for offline/error backends */
	:global(.backend-card-offline) {
		animation: backend-pulse-offline 2s ease-in-out infinite;
		border-color: rgb(var(--danger-solid) / 0.5) !important;
	}

	@keyframes backend-pulse-offline {
		0%, 100% {
			box-shadow: 0 0 2px rgb(var(--danger-solid) / 0.2), 0 0 6px rgb(var(--danger-solid) / 0.1);
		}
		50% {
			box-shadow: 0 0 4px rgb(var(--danger-solid) / 0.35), 0 0 10px rgb(var(--danger-solid) / 0.15);
		}
	}

	:global(.health-dot-pulse) {
		animation: health-dot-pulse 2s ease-in-out infinite;
		display: inline-block;
	}

	@keyframes health-dot-pulse {
		0%, 100% {
			transform: scale(1);
			opacity: 1;
		}
		50% {
			transform: scale(1.1);
			opacity: 0.8;
		}
	}
</style>

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="Backends"
		icon="cpu"
		counts={[
			{ label: 'backends', value: totalBackends },
			{ label: 'healthy', value: healthyBackends, tone: 'success' },
			{ label: 'enabled', value: enabledBackends, tone: 'info' }
		]}
	>
		{#snippet actions()}
			<Button variant="primary" size="sm" icon="plus" onclick={openCreateModal}>Add Backend</Button>
		{/snippet}
	</AdminTabShell>

	{#snippet backendSearch()}
		<div class="relative">
			<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
			<Input bind:value={searchQuery} type="search" class="pl-9" placeholder="Search by name or engine…" aria-label="Search backends" />
		</div>
	{/snippet}
	{#snippet backendSearchTrailing()}
		<span class="text-sm text-fg-muted whitespace-nowrap font-mono tabular-nums">{filteredBackends.length} {filteredBackends.length === 1 ? 'backend' : 'backends'}</span>
	{/snippet}

	<AdminFilterBar
		search={backendSearch}
		trailing={backendSearchTrailing}
		activeCount={searchQuery ? 1 : 0}
		onClear={() => (searchQuery = '')}
	/>

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		{#if loading}
			<div class="h-full flex flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading backends…</p>
			</div>
		{:else if error}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState title="Error loading backends" description={error ?? ''} icon="warning" compact>
					{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadBackends}>Try again</Button>{/snippet}
				</EmptyState>
			</div>
		{:else if backends.length === 0}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState
					icon="cpu"
					title="No backends configured yet"
					description="A backend tells PotionUI where to run generations for an engine — for example, the built-in native engine or a ComfyUI server. Add one to start generating."
					compact
				>
					{#snippet actions()}
						<Button variant="primary" size="sm" icon="plus" onclick={openCreateModal}>Add Backend</Button>
					{/snippet}
				</EmptyState>
			</div>
		{:else}
			<MasterDetailLayout leftWidth={340} minWidth={280} maxWidth={480} storageKey="admin-backends-width">
				<div slot="list" class="h-full min-h-0">
					<Pane
						label="Backends"
						count={filteredBackends.length}
						isEmpty={filteredBackends.length === 0}
						bodyRole="listbox"
						ariaLabel="Backends"
					>
						{#snippet empty()}
							<div class="p-4 h-full flex items-center justify-center">
								<EmptyState title="No backends match your search" description="Try a different name or engine." icon="search" compact>
									{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => (searchQuery = '')}>Clear search</Button>{/snippet}
								</EmptyState>
							</div>
						{/snippet}

						{#snippet children()}
							{#each backendsByEngine as group (group.engine)}
								<PaneGroupHeader label={formatEngineName(group.engine)} count={group.items.length} />
								{#each group.items as backend (backend.id)}
									{@const health = backendsHealth.find((h) => h.backend_id === backend.id)}
									{#snippet backendLeading()}
										<span
											class="w-2 h-2 rounded-full flex-shrink-0 {health ? dotColorClasses[getHealthVariant(health.health.status)] : 'bg-line-strong'}"
											title={health ? health.health.status : 'Health unknown'}
										></span>
									{/snippet}
									{#snippet backendBadges()}
										{#if backend.is_default}<Badge variant="signal" size="sm">Default</Badge>{/if}
									{/snippet}
									<PaneRow
										selected={selectedBackendId === backend.id}
										onclick={() => selectBackend(backend.id)}
										leading={backendLeading}
										title={backend.name}
										subtitle={backend.host ? `${backend.host}:${backend.port}` : formatEngineName(backend.engine)}
										subtitleMono
										badges={backendBadges}
									/>
								{/each}
							{/each}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					{#if activeBackend}
						{@const backendIsOffline = activeHealth && (activeHealth.health.status === 'offline' || activeHealth.health.status === 'error')}
						<div class="flex flex-wrap items-center gap-2 px-4 sm:px-5 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
							<nav class="inline-flex items-center gap-1" aria-label="Backend details">
								<button
									type="button"
									class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'overview' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
									on:click={() => (detailTab = 'overview')}
									aria-current={detailTab === 'overview' ? 'page' : undefined}
								><Icon name="info" className="w-3.5 h-3.5" />Overview</button>
								{#if activeBackend.driver === NATIVE_LOCAL_DRIVER}
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'optimizations' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (detailTab = 'optimizations')}
										aria-current={detailTab === 'optimizations' ? 'page' : undefined}
									><Icon name="sliders" className="w-3.5 h-3.5" />Optimizations</button>
								{/if}
								<button
									type="button"
									class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'stats' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
									on:click={() => (detailTab = 'stats')}
									aria-current={detailTab === 'stats' ? 'page' : undefined}
								><Icon name="gauge" className="w-3.5 h-3.5" />Stats</button>
							</nav>
							<div class="ml-auto flex items-center gap-2 flex-wrap">
									<Switch
										checked={activeBackend.enabled}
										busy={togglingBackendId === activeBackend.id}
										size="lg"
										onchange={() => activeBackend && toggleEnabled(activeBackend)}
										label="Backend enabled"
									/>
									{#if !activeBackend.is_default}
									<Tooltip text="Make default">
										<button type="button" aria-label="Make default" class="inline-flex items-center justify-center min-w-8 min-h-8 p-1.5 rounded transition-colors duration-100 text-fg-muted hover:text-fg hover:bg-surface-3/50" on:click={() => activeBackend && makeDefault(activeBackend)}>
											<Icon name="star" className="w-4 h-4" />
										</button>
									</Tooltip>
								{/if}
									<Tooltip text={indexingBackendId === activeBackend.id ? 'Indexing…' : 'Index models'}>
									<button type="button" aria-label="Index models" class="inline-flex items-center justify-center min-w-8 min-h-8 p-1.5 rounded transition-colors duration-100 text-fg-muted hover:text-fg hover:bg-surface-3/50 disabled:opacity-50 disabled:cursor-not-allowed" disabled={indexingBackendId !== null && indexingBackendId !== activeBackend.id} on:click={() => activeBackend && indexModels(activeBackend)}>
										{#if indexingBackendId === activeBackend.id}<Spinner size="sm" />{:else}<Icon name="refresh" className="w-4 h-4" />{/if}
									</button>
								</Tooltip>
									<Tooltip text="Test connection">
									<button type="button" aria-label="Test connection" class="inline-flex items-center justify-center min-w-8 min-h-8 p-1.5 rounded transition-colors duration-100 text-fg-muted hover:text-fg hover:bg-surface-3/50 disabled:opacity-50 disabled:cursor-not-allowed" disabled={testing} on:click={() => activeBackend && testConnection(activeBackend.id)}>
										{#if testing}<Spinner size="sm" />{:else}<Icon name="check" className="w-4 h-4" />{/if}
									</button>
								</Tooltip>
									<BackendQuickActions
										actions={activeBackend.quick_actions ?? []}
										backendName={activeBackend.name}
										onDone={() => {
											loadBackends();
											loadBackendsHealth();
										}}
									/>
									{#if activeBackend.driver !== NATIVE_LOCAL_DRIVER}
									<Tooltip text="Delete backend">
										<button type="button" aria-label="Delete backend" class="inline-flex items-center justify-center min-w-8 min-h-8 p-1.5 rounded transition-colors duration-100 text-danger hover:text-danger hover:bg-danger/10" on:click={() => activeBackend && openDeleteModal(activeBackend)}>
											<Icon name="trash" className="w-4 h-4" />
										</button>
									</Tooltip>
								{/if}
									<div class="h-5 w-px bg-line-strong mx-1"></div>
									{#if editDirty}<Badge variant="warning" size="sm" dot>Unsaved</Badge>{/if}
							</div>
						</div>

						<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2 {backendIsOffline ? 'backend-card-offline' : ''}">
							{#if detailTab === 'overview'}
								<div class="p-4 sm:p-5 space-y-5">
									<div class="flex items-center gap-2 flex-wrap">
										<h2 class="text-base font-semibold text-fg truncate flex items-center gap-2">
											{activeBackend.name}
											{#if activeIsHealthy && activeBackend.enabled}
												<span class="relative flex h-2.5 w-2.5">
													<span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success-solid opacity-75"></span>
													<span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-success-solid"></span>
												</span>
											{/if}
										</h2>
										<Badge variant="neutral" size="sm" class="font-mono uppercase">{formatDriverLabel(activeBackend.driver)}</Badge>
										{#if activeBackend.is_default}<Badge variant="signal" size="sm" class="uppercase">Default</Badge>{/if}
										{#if activeHealth}<Badge variant={getHealthVariant(activeHealth.health.status)} size="sm" dot class="{activeIsHealthy ? 'health-dot-pulse' : ''} uppercase">{activeHealth.health.status}</Badge>{/if}
									</div>

									<!-- Test Connection Result -->
									{#if testResult}
										<Alert variant={testResult.success ? 'success' : 'danger'} density="compact" title={testResult.success ? 'Connection successful' : 'Connection failed'}>
											{#if testResult.message}{testResult.message}{/if}
										</Alert>
									{/if}

									<!-- Model Indexing: cannot-list notice, or the last run's summary + warnings -->
									{#if indexUnsupported[activeBackend.id]}
										<Alert variant="warning" density="compact">
											{indexUnsupported[activeBackend.id]}
										</Alert>
									{:else if indexResults[activeBackend.id]}
										{@const result = indexResults[activeBackend.id]}
										{@const warningCount =
											result.size_conflicts.length +
											result.digest_conflicts.length +
											result.ambiguous.length}
										{@const hasDigestConflicts = result.digest_conflicts.length > 0}
										<div class="rounded border border-line bg-surface-1 px-3 py-2 space-y-2">
											<p class="text-xs font-mono tabular-nums text-fg-muted">
												Indexed {result.listed} models — {result.created} new, {result.matched} matched,
												{result.removed} removed
											</p>
											{#if warningCount > 0}
												<button
													type="button"
													class="flex items-center gap-1.5 text-2xs font-mono uppercase tracking-[0.05em] {hasDigestConflicts
														? 'text-danger'
														: 'text-warning'} hover:underline"
													on:click={() => toggleIndexWarnings(activeBackend.id)}
												>
													<Badge variant={hasDigestConflicts ? 'danger' : 'warning'} size="sm" dot>
														{warningCount} warning{warningCount === 1 ? '' : 's'}
													</Badge>
													<span>{indexWarningsOpen[activeBackend.id] ? 'Hide' : 'Show'}</span>
												</button>
												{#if indexWarningsOpen[activeBackend.id]}
													<ul class="space-y-1.5 text-2xs text-fg-subtle leading-relaxed">
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
														{#each result.ambiguous as note}
															<li class="font-mono">{note}</li>
														{/each}
													</ul>
												{/if}
											{/if}
										</div>
									{/if}

									<!-- Section cards matching System Settings' panel style — a WQHD
									     pane keeps the control right under its label instead of
									     stranded far to the right. Capped to a readable column so
									     controls never stretch across an ultrawide pane. -->
									<div class="max-w-2xl space-y-5">
										{#key activeBackend.id}
											<BackendInfrastructureSection
												backendId={activeBackend.id}
												onStopped={() => {
													loadBackends();
													loadBackendsHealth();
												}}
												onTerminated={handleInfrastructureTerminated}
											/>
										{/key}

										<BackendForm
											bind:draft={editFormData}
											mode="edit"
											layout="panel"
											idPrefix="edit-backend"
											engineMutable={false}
											engineLabel={formatDriverLabel(activeBackend.driver)}
											fieldDescriptors={activeEditEngineFields}
											enabledPlacement="none"
										/>

										<!-- Save/Discard live at the bottom of the form, same as System
										     Settings' own trailing save row. -->
										<div class="flex items-center justify-end gap-2">
											<Button variant="ghost" size="sm" disabled={!editDirty} onclick={discardEditForm}>Discard</Button>
											<Button variant="primary" size="sm" loading={editSaving} disabled={!editDirty} onclick={saveEditForm}>Save</Button>
										</div>
									</div>
								</div>
							{:else if detailTab === 'optimizations' && activeBackend.driver === NATIVE_LOCAL_DRIVER}
								<div class="p-4 sm:p-5">
									{#key activeBackend.id}
										<BackendOptimizations backendId={activeBackend.id} />
									{/key}
								</div>
							{:else if detailTab === 'stats'}
								<div class="p-4 sm:p-5">
									{#if backendStatsLoading}
										<div class="flex items-center justify-center py-12">
											<Spinner size="lg" />
										</div>
									{:else if backendStatsError}
										<EmptyState title="Stats unavailable" description={backendStatsError} icon="warning" compact>
											{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={() => activeBackend && loadBackendStats(activeBackend.id)}>Try again</Button>{/snippet}
										</EmptyState>
									{:else if backendStats}
										<div class="max-w-2xl grid grid-cols-1 sm:grid-cols-3 gap-4">
											<div class="bg-surface-1 rounded-lg border border-line shadow-raised p-4 text-center">
												<div class="text-2xl font-mono tabular-nums font-semibold text-fg">{backendStats.indexed_models}</div>
												<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted mt-1">Indexed Models</div>
											</div>
											<div class="bg-surface-1 rounded-lg border border-line shadow-raised p-4 text-center">
												<div class="text-2xl font-mono tabular-nums font-semibold text-fg">{backendStats.total_size_gb.toFixed(1)} GB</div>
												<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted mt-1">Total Size</div>
											</div>
											<div class="bg-surface-1 rounded-lg border border-line shadow-raised p-4 text-center">
												<div class="text-2xl font-mono tabular-nums font-semibold text-fg">
													{backendStats.last_indexed_at ? timeAgo(backendStats.last_indexed_at) : 'Never'}
												</div>
												<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted mt-1">Last Indexed</div>
											</div>
										</div>
										{#if backendStats.indexed_models === 0}
											<p class="text-sm text-fg-muted mt-4 max-w-2xl">
												This backend hasn't been indexed yet — these numbers come from its own
												index, not a live scan. Use "Index models" above to populate them.
											</p>
										{/if}
									{/if}
								</div>
							{/if}
						</div>
					{:else}
						<div class="h-full p-5 flex items-center justify-center">
							<EmptyState
								title="No backend selected"
								description="Choose a backend from the list to see and edit its details."
								icon="cpu"
								compact
							/>
						</div>
					{/if}
				</div>
			</MasterDetailLayout>
		{/if}
	</section>
</div>


<!-- Create Backend Modal (create only — editing happens directly in the detail pane) -->
<BaseModal
	isOpen={showModal}
	title="Add Backend"
	sizeClass="md:max-w-2xl md:w-full"
	on:close={closeModal}
>
	<div class="px-6 py-4">
		{#if showCreateSourceToggle}
			<div class="mb-5">
				<SegmentedControl
					items={[
						{ id: 'connect', label: 'Connect to existing worker', icon: 'cpu' },
						{ id: 'provision', label: 'Provision new compute', icon: 'zap' }
					]}
					selected={createSource}
					onSelect={(id) => (createSource = id as CreateSource)}
					ariaLabel="Remote worker source"
				/>
			</div>
		{/if}

		{#if formData.driver === NATIVE_REMOTE_DRIVER && createSource === 'provision'}
			<RemoteWorkerProvisionForm {providers} onCreated={handleRemoteWorkerCreated} onCancel={closeModal} />
		{:else}
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
			/>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		{#if !(formData.driver === NATIVE_REMOTE_DRIVER && createSource === 'provision')}
			<div class="px-6 py-4 flex gap-3">
				<Button variant="primary" class="flex-1" loading={saving} onclick={saveBackend}>
					{saving ? 'Creating…' : 'Create Backend'}
				</Button>
				<Button variant="secondary" onclick={closeModal}>Cancel</Button>
			</div>
		{/if}
	</svelte:fragment>
</BaseModal>

<!-- Delete Confirmation Modal -->
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
