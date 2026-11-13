<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import dismiss from '$lib/actions/dismiss';
	import { goto } from '$app/navigation';
	import { createUrlState, hrefWithQuery } from '$lib/utils/urlState';
	import { api } from '$lib/services/api/index';
	import * as adminApi from '$lib/services/admin-api';
	import { invalidatePresets } from '$lib/stores/presetsCatalog';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { logger } from '$lib/utils/logger';
	import { hasPresetMedia } from '$lib/utils/presetMedia';
	import { presetRequirementsBadge } from '$lib/utils/presetRequirementsBadge';
	import { presetCategoryIcon, presetCategoryLabel } from '$lib/utils/presetCategories';
	import { processMarkdown } from '$lib/utils/markdown';
	import { formatBytes } from '$lib/utils/format';
	import { RecipeRunSession } from '$lib/components/recipes/recipeRunSession.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import { PaneRow } from '$lib/components/pane';
	import { DetailHeader, DetailTabs, DetailBody, DetailLayout, DetailSection, DetailFooter, KVGrid, KVItem, DETAIL_INSET_CLASS } from '$lib/components/detail';
	import PresetThumbnail from '$lib/components/preset/PresetThumbnail.svelte';
	import PresetExampleCard from '$lib/components/preset/PresetExampleCard.svelte';
	import PresetMediaModal from '$lib/components/preset/PresetMediaModal.svelte';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import { createPresetAssignmentAdapter } from '$lib/components/assignment/presetAssignmentAdapter';
	import PresetConfigurationTab from './PresetConfigurationTab.svelte';
	import PresetFormOverridesTab from './PresetFormOverridesTab.svelte';
	import PresetRequirementsTab from './presets/PresetRequirementsTab.svelte';
	import PresetCard from './presets/PresetCard.svelte';
	import PresetRecipeSetup from './presets/PresetRecipeSetup.svelte';
	import PresetModesList from './presets/PresetModesList.svelte';
	import PresetFiltersPopover from './presets/PresetFiltersPopover.svelte';
	import PresetDisplayTab from './presets/PresetDisplayTab.svelte';
	import PresetPromptTextTab from './presets/PresetPromptTextTab.svelte';
	import CreateMirrorModal from './presets/CreateMirrorModal.svelte';
	import PresetHasMirrorsDialog from './presets/PresetHasMirrorsDialog.svelte';
	import { presetApiFailure, runPresetUninstall } from './presets/presetApiFailure';
	import {
		isUnavailableMirror,
		mirrorSourceName,
		mirrorStateCopy,
		presetHeaderChips
	} from './presets/presetMirrorState';
	import { PRESET_LIBRARY_SECTIONS, type PresetLibrarySection } from './presets/presetLibrarySections';
	import {
		DEFAULT_PRESET_DETAIL_TAB,
		PRESET_DETAIL_TAB_PARAM,
		presetDetailTabsFor,
		resolvePresetDetailTab,
		restorePresetDetailTab,
		type PresetDetailTab
	} from './presets/presetDetailTabs';
	import {
		PRESET_SORT_OPTIONS,
		applyPresetFilters,
		presetEngineCounts,
		presetEngines,
		presetSectionCounts,
		type PresetFilters,
		type PresetSortBy,
		presetFilters
	} from './presets/presetFilters';
	import { Badge, Button, EmptyState, Spinner, Alert, IconButton } from '$lib/components/ui';
	import type {
		PresetInfo,
		PresetAdminDetail,
		PresetConfigurationEntry,
		PresetMirrorRef,
		PresetRecipeLink
	} from '$lib/types/api';

	type PresetDetailInfo = PresetInfo & { vars?: Record<string, unknown> };

	let presets = $state<PresetInfo[]>([]);
	let loading = $state(true);
	let refreshing = $state(false);
	let loadError = $state('');
	let detailTab = $state<PresetDetailTab>(DEFAULT_PRESET_DETAIL_TAB);
	let presetDetail = $state<PresetDetailInfo | null>(null);
	let detailLoading = $state(false);
	let detailError = $state('');
	let mutatingPresetId = $state<string | null>(null);
	let mediaModalOpen = $state(false);
	let presetConfigEntries = $state<PresetConfigurationEntry[]>([]);
	let configEntriesSettled = $state(true);
	let lastViewId: string | null = null;
	let detailRequestVersion = 0;
	let headerOverflowOpen = $state(false);
	let headerOverflowEl: HTMLDivElement | undefined = $state();
	let configurationTabRef: any = $state();
	let configDirtyCount = $state(0);
	let configSaving = $state(false);
	let formTabRef: any = $state();
	let formDirtyCount = $state(0);
	let formSaving = $state(false);
	let displayTabRef: any = $state();
	let displayDirtyCount = $state(0);
	let displaySaving = $state(false);
	let promptTabRef: any = $state();
	let promptDirtyCount = $state(0);
	let promptSaving = $state(false);
	let adminDetail = $state<PresetAdminDetail | null>(null);
	let adminDetailError = $state('');
	let adminRequestVersion = 0;
	let createMirrorModal: any = $state();
	let blockedUninstall = $state<{ name: string; mirrors: PresetMirrorRef[] } | null>(null);

	const section = $derived(($page.url.searchParams.get('section') as PresetLibrarySection) || 'all');
	const filters = $derived(presetFilters.fromSearchParams($page.url.searchParams));
	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);

	const engines = $derived(presetEngines(presets));
	const engineCounts = $derived(presetEngineCounts(presets));
	const sectionCounts = $derived(presetSectionCounts(presets));
	const visiblePresets = $derived(applyPresetFilters(presets, filters, section));
	const chips = $derived(presetFilters.chips(filters));
	const activeFilterCount = $derived(presetFilters.activeCount(filters));

	const presetsById = $derived(new Map(presets.map((preset) => [preset.id, preset])));
	const selectedPreset = $derived(viewId ? (presets.find((preset) => preset.id === viewId) ?? null) : null);
	const activePreset = $derived(
		selectedPreset && presetDetail?.id === selectedPreset.id
			? {
					...selectedPreset,
					...presetDetail,
					installed: selectedPreset.installed,
					assignment_count: selectedPreset.assignment_count,
					group_count: selectedPreset.group_count,
					media: {
						...(selectedPreset.media || {}),
						...(presetDetail.media || {})
					}
				}
			: selectedPreset
	);
	const descriptionHtml = $derived(activePreset?.description ? processMarkdown(activePreset.description) : '');
	const gallery = $derived(activePreset?.media?.gallery || []);
	const presetRecipes = $derived(activePreset?.recipes ?? []);
	const headerRecipe = $derived(
		presetRecipes.length && !(activePreset?.installed && presetRecipes[0].readiness === 'installed')
			? presetRecipes[0]
			: null
	);
	const requirementsBadge = $derived(presetRequirementsBadge(activePreset?.requirements_summary));
	const detailTabs = $derived(activePreset ? presetDetailTabsFor(activePreset, presetConfigEntries.length > 0) : []);
	const effectiveTab = $derived(resolvePresetDetailTab(detailTabs, detailTab));
	const headerChips = $derived(activePreset ? presetHeaderChips(activePreset) : []);
	const activeUnavailable = $derived(isUnavailableMirror(activePreset));
	const activeStateCopy = $derived(activePreset?.is_mirror ? mirrorStateCopy(activePreset.mirror_state) : null);
	const activeAdminDetail = $derived(adminDetail && activePreset && adminDetail.preset_id === activePreset.id ? adminDetail : null);
	const activeSourceName = $derived(
		activePreset?.is_mirror
			? (activeAdminDetail?.source_name ?? mirrorSourceName(activePreset, presetsById))
			: null
	);
	const activeSourceAvailable = $derived(
		!!activePreset?.source_preset_id && presetsById.has(activePreset.source_preset_id)
	);
	const renamedFrom = $derived(
		activePreset && !activePreset.is_mirror && activePreset.display_overridden && activePreset.file_name && activePreset.file_name !== activePreset.name
			? activePreset.file_name
			: null
	);
	const canCreateMirror = $derived(!!activePreset?.installed && !activePreset.is_mirror);
	const activeMirrors = $derived(activeAdminDetail?.mirrors ?? []);

	const recipeSession = new RecipeRunSession({
		onFinished: () => {
			invalidatePresets();
			void loadPresets(true);
			if (viewId) loadPresetDetail(viewId);
		}
	});

	onDestroy(() => recipeSession.dispose());

	function startRecipe(recipe: PresetRecipeLink) {
		selectDetailTab(DEFAULT_PRESET_DETAIL_TAB);
		void recipeSession.start(recipe.id);
	}

	onMount(async () => {
		await loadPresets();
		const presetIdParam = $page.url.searchParams.get('preset');
		if (presetIdParam && presets.some((preset) => preset.id === presetIdParam)) {
			const url = new URL($page.url);
			url.searchParams.delete('preset');
			url.searchParams.set('tab', 'presets');
			url.searchParams.set('id', presetIdParam);
			void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		}
	});

	$effect(() => {
		if (loading) return;
		if (viewId === untrack(() => lastViewId)) return;
		lastViewId = viewId;
		untrack(() => {
			const opened = presets.find((preset) => preset.id === viewId);
			const unavailable = isUnavailableMirror(opened);
			detailTab = restorePresetDetailTab($page.url.searchParams.get(PRESET_DETAIL_TAB_PARAM));
			configEntriesSettled = !opened?.installed || unavailable;
			recipeSession.reset();
			presetDetail = null;
			detailError = '';
			mediaModalOpen = false;
			presetConfigEntries = [];
			headerOverflowOpen = false;
			configDirtyCount = 0;
			formDirtyCount = 0;
			displayDirtyCount = 0;
			promptDirtyCount = 0;
			adminDetail = null;
			adminDetailError = '';
			blockedUninstall = null;
			if (viewId) {
				if (!unavailable) loadPresetDetail(viewId);
				if (opened?.installed) loadAdminDetail(viewId);
				if (opened?.installed && !unavailable) loadPresetConfigEntries(viewId);
			}
		});
	});

	$effect(() => {
		if (!activePreset || !viewId || detailTab === effectiveTab) return;
		if (detailTab === 'configuration' && !configEntriesSettled) return;
		untrack(() => selectDetailTab(DEFAULT_PRESET_DETAIL_TAB));
	});

	function responseError(response: { message?: string } | null | undefined, fallback: string) {
		return response?.message || fallback;
	}

	async function loadPresets(background = false) {
		if (background) refreshing = true;
		else loading = true;
		loadError = '';
		try {
			const response = await api.listPresets(true);
			if (!response.success) {
				throw new Error(responseError(response, 'Could not load the preset catalog'));
			}
			presets = response.data || [];
		} catch (error) {
			logger.error('Failed to load presets:', error);
			loadError = error instanceof Error ? error.message : 'Could not load the preset catalog';
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	async function loadPresetConfigEntries(id: string) {
		try {
			const response = await api.getPresetConfiguration(id);
			if (response.success && response.data && id === viewId) {
				presetConfigEntries = response.data.entries || [];
			}
		} catch (error) {
			logger.error('Failed to load preset configuration entries:', error);
		} finally {
			if (id === viewId) configEntriesSettled = true;
		}
	}

	async function loadPresetDetail(id: string) {
		const version = ++detailRequestVersion;
		detailLoading = true;
		detailError = '';
		try {
			const response = await api.getPreset(id);
			if (!response.success || !response.data) {
				throw new Error(responseError(response, 'Could not load preset details'));
			}
			if (version !== detailRequestVersion || id !== viewId) return;
			presetDetail = response.data;
		} catch (error) {
			if (version !== detailRequestVersion || id !== viewId) return;
			logger.error('Failed to load preset details:', error);
			detailError = error instanceof Error ? error.message : 'Could not load preset details';
		} finally {
			if (version === detailRequestVersion && id === viewId) detailLoading = false;
		}
	}

	async function loadAdminDetail(id: string) {
		const version = ++adminRequestVersion;
		adminDetailError = '';
		try {
			const response = await api.getPresetAdmin(id);
			if (!response.success || !response.data) {
				throw new Error(responseError(response, 'Could not load the display settings'));
			}
			if (version !== adminRequestVersion || id !== viewId) return;
			adminDetail = response.data;
		} catch (error) {
			if (version !== adminRequestVersion || id !== viewId) return;
			logger.error('Failed to load preset admin detail:', error);
			adminDetailError = presetApiFailure(error, 'Could not load the display settings').message;
		}
	}

	function handleDisplayChanged(next: PresetAdminDetail) {
		adminDetail = next;
		void loadPresets(true);
		if (viewId === next.preset_id && !activeUnavailable) loadPresetDetail(next.preset_id);
	}

	function openCreateMirror() {
		if (!activePreset) return;
		createMirrorModal?.open(
			{ id: activePreset.id, name: activePreset.name, description: activePreset.description ?? null },
			activeAdminDetail?.modes.available ?? []
		);
	}

	async function handleMirrorCreated(created: PresetAdminDetail) {
		invalidatePresets();
		await loadPresets(true);
		void urlState.go({ id: created.preset_id, view: 'access' });
	}

	function buildUrl(
		overrides: { section?: PresetLibrarySection; id?: string | null; filters?: PresetFilters; view?: PresetDetailTab } = {}
	): string {
		const params = presetFilters.toSearchParams(overrides.filters ?? filters);
		params.set('tab', 'presets');
		const nextSection = overrides.section ?? section;
		if (nextSection !== 'all') params.set('section', nextSection);
		const id = overrides.id !== undefined ? overrides.id : viewId;
		if (id) params.set('id', id);
		const view = overrides.view ?? (overrides.id === undefined ? detailTab : DEFAULT_PRESET_DETAIL_TAB);
		if (id && view !== DEFAULT_PRESET_DETAIL_TAB) params.set(PRESET_DETAIL_TAB_PARAM, view);
		return hrefWithQuery($page.url.pathname, params);
	}

	const urlState = createUrlState({ href: buildUrl });
	onDestroy(urlState.cancel);

	function selectDetailTab(tab: PresetDetailTab) {
		detailTab = tab;
		if (viewId) void urlState.replace({ view: tab });
	}

	function updateFilters(next: PresetFilters) {
		urlState.debounced({ filters: next });
	}

	function selectSection(id: PresetLibrarySection) {
		void urlState.go({ section: id, id: null });
	}

	function toggleEngineFilter(engine: string) {
		const nextEngine = filters.engine === engine ? '' : engine;
		void urlState.go({ id: null, filters: { ...filters, engine: nextEngine } });
	}

	function openPresetId(id: string) {
		void urlState.go({ id });
	}

	function openPreset(preset: PresetInfo) {
		openPresetId(preset.id);
	}

	function backToGrid() {
		void urlState.go({ id: null });
	}

	async function handleInstall(preset: PresetInfo) {
		mutatingPresetId = preset.id;
		try {
			const response = await adminApi.installPreset(preset.id);
			if (!response.success) {
				throw new Error(responseError(response, 'The preset could not be installed'));
			}
			toasts.success(`${preset.name} installed`);
			invalidatePresets();
			await loadPresets(true);
			if (viewId === preset.id) {
				selectDetailTab('access');
				loadPresetConfigEntries(preset.id);
			}
		} catch (error) {
			logger.error('Failed to install preset:', error);
			toasts.error(error instanceof Error ? error.message : 'Failed to install preset');
		} finally {
			mutatingPresetId = null;
		}
	}

	async function handleUninstall(preset: PresetInfo) {
		const accessCount = (preset.assignment_count || 0) + (preset.group_count || 0);
		const assignmentWarning = accessCount
			? ` This will also remove ${accessCount} access ${accessCount === 1 ? 'assignment' : 'assignments'}.`
			: '';
		const isMirror = !!preset.is_mirror;
		if (
			!(await confirmDialog({
				title: isMirror ? `Remove “${preset.name}”?` : `Uninstall “${preset.name}”?`,
				message: isMirror
					? `Users lose this preset. Its source keeps working.${assignmentWarning}`
					: assignmentWarning.trim(),
				variant: 'danger',
				confirmLabel: isMirror ? 'Remove' : 'Uninstall'
			}))
		)
			return;

		mutatingPresetId = preset.id;
		try {
			const outcome = await runPresetUninstall(
				() => adminApi.uninstallPreset(preset.id),
				isMirror ? 'The mirror could not be removed' : 'The preset could not be uninstalled'
			);
			if (outcome.kind === 'has_mirrors') {
				const mirrors = outcome.mirrors.length
					? outcome.mirrors
					: (activeAdminDetail?.mirrors ?? []).map((mirror) => ({ preset_id: mirror.preset_id, name: mirror.name }));
				blockedUninstall = { name: preset.name, mirrors };
				return;
			}
			if (outcome.kind === 'refused') {
				toasts.error(outcome.message);
				return;
			}
			toasts.success(isMirror ? `${preset.name} removed` : `${preset.name} uninstalled`);
			selectDetailTab(DEFAULT_PRESET_DETAIL_TAB);
			invalidatePresets();
			await loadPresets(true);
			if (isMirror && viewId === preset.id) backToGrid();
		} catch (error) {
			logger.error('Failed to uninstall preset:', error);
			toasts.error(error instanceof Error ? error.message : 'Failed to uninstall preset');
		} finally {
			mutatingPresetId = null;
		}
	}

	function openBlockingMirror(id: string) {
		blockedUninstall = null;
		openPresetId(id);
	}

	function closeHeaderOverflow() {
		headerOverflowOpen = false;
	}

	function handleAccessChanged(presetId: string, event: CustomEvent<{ userCount: number; groupCount: number }>) {
		presets = presets.map((preset) =>
			preset.id === presetId
				? {
						...preset,
						assignment_count: event.detail.userCount,
						group_count: event.detail.groupCount
					}
				: preset
		);
	}
</script>

<LibraryShell
	title="Presets"
	persistKey="admin-presets-library"
	heightClass="h-full"
	sections={PRESET_LIBRARY_SECTIONS}
	{section}
	onSelectSection={selectSection}
	{sectionCounts}
	count={visiblePresets.length}
	{detailOpen}
	filterChips={chips}
	onRemoveChip={(key) => updateFilters(presetFilters.clearChip(filters, key))}
	onClearFilters={() => updateFilters(presetFilters.clearAll(filters))}
	loadedCount={visiblePresets.length}
	total={presets.length}
>
	{#snippet sidebarTree()}
		{#if engines.length}
			<div class="border-t border-line">
				<div class="px-3 pb-1 pt-2 font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">By engine</div>
				<div class="space-y-0.5 p-2 pt-0">
					{#each engines as engine (engine)}
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
			onQueryChange={(value) => updateFilters({ ...filters, q: value })}
			searchPlaceholder="Search presets by name, id, or tag…"
			sortBy={filters.sortBy}
			sortOptions={PRESET_SORT_OPTIONS}
			onSortChange={(value) => updateFilters({ ...filters, sortBy: value as PresetSortBy })}
			filterCount={activeFilterCount}
		>
			{#snippet popover(close: () => void)}
				<PresetFiltersPopover {filters} {engines} onChange={updateFilters} onClose={close} />
			{/snippet}
		</LibraryFilterBar>
	{/snippet}

	{#snippet primary()}
		<PluginSlot hookName="admin.presets.header-actions" context={{ selectPreset: openPresetId, refreshPresets: () => loadPresets(true) }} />
		<Button variant="secondary" size="sm" icon="refresh" loading={refreshing} onclick={() => loadPresets(true)}>Refresh catalog</Button>
	{/snippet}

	{#if detailOpen}
		{#if !activePreset}
			<div class="flex h-full items-center justify-center">
				{#if detailLoading || loading}
					<Spinner size="lg" />
				{:else}
					<EmptyState title="Preset not found" description="This preset may have been removed from the catalog." icon="cube" compact>
						{#snippet actions()}<Button size="sm" variant="secondary" onclick={backToGrid}>Back to presets</Button>{/snippet}
					</EmptyState>
				{/if}
			</div>
		{:else}
			<div class="flex h-full flex-col">
				<DetailHeader title={activePreset.name} icon={presetCategoryIcon(activePreset.category)} backLabel="Presets" onBack={backToGrid} chipItems={headerChips}>
					{#snippet subtitle()}
						{#if activePreset.is_mirror}
							<span>Mirror of</span>
							{#if activeSourceAvailable && activePreset.source_preset_id}
								<button
									type="button"
									class="rounded text-signal hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-signal/40"
									data-mirror-source-link
									onclick={() => activePreset.source_preset_id && openPresetId(activePreset.source_preset_id)}
								>{activeSourceName}</button>
							{:else}
								<span class="text-warning">{activeSourceName ?? 'a missing preset'}</span>
							{/if}
						{:else if renamedFrom}
							<span>Renamed from {renamedFrom}</span>
						{/if}
					{/snippet}
					{#snippet actions()}
						{#if headerRecipe}
							<Tooltip text={headerRecipe.name}>
								<Button
									variant="primary"
									size="sm"
									icon="download"
									loading={recipeSession.starting}
									disabled={recipeSession.starting || recipeSession.inFlight}
									onclick={() => startRecipe(headerRecipe)}
								>
									Set up with recipe{#if headerRecipe.total_download_bytes != null}<span class="ml-1.5 font-mono tabular-nums">{formatBytes(headerRecipe.total_download_bytes)}</span>{/if}
								</Button>
							</Tooltip>
						{:else if !activePreset.installed}
							<Button
								variant="primary"
								size="sm"
								icon="download"
								loading={mutatingPresetId === activePreset.id}
								disabled={mutatingPresetId !== null}
								onclick={() => handleInstall(activePreset)}
							>Install preset</Button>
						{/if}
						{#if activePreset.installed}
							<div class="relative" bind:this={headerOverflowEl}>
								<Tooltip text="More actions">
									<IconButton
										icon="more"
										label="More actions"
										size="sm"
										ariaExpanded={headerOverflowOpen}
										active={headerOverflowOpen}
										onclick={() => (headerOverflowOpen = !headerOverflowOpen)}
									/>
								</Tooltip>
								{#if headerOverflowOpen}
									<div
										class="absolute right-0 top-[calc(100%+6px)] z-40 min-w-[180px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating"
										role="menu"
										use:dismiss={{ onDismiss: closeHeaderOverflow, ignore: [headerOverflowEl] }}
									>
										{#if canCreateMirror}
											<button
												type="button"
												role="menuitem"
												class="w-full px-3 py-2 text-left text-xs flex items-center gap-2 text-fg hover:bg-surface-3 disabled:opacity-50"
												disabled={mutatingPresetId !== null}
												data-create-mirror-action
												onclick={() => {
													closeHeaderOverflow();
													openCreateMirror();
												}}
											>
												<Icon name="layers" className="w-3.5 h-3.5" />
												Create mirror
											</button>
										{/if}
										<button
											type="button"
											role="menuitem"
											class="w-full px-3 py-2 text-left text-xs flex items-center gap-2 text-danger hover:bg-danger/10 disabled:opacity-50"
											disabled={mutatingPresetId !== null}
											onclick={() => {
												closeHeaderOverflow();
												handleUninstall(activePreset);
											}}
										>
											<Icon name="trash" className="w-3.5 h-3.5" />
											{activePreset.is_mirror ? 'Remove mirror' : 'Uninstall'}
										</button>
									</div>
								{/if}
							</div>
						{/if}
					{/snippet}
				</DetailHeader>

				<DetailTabs
					tabs={detailTabs}
					active={effectiveTab}
					onSelect={(id) => selectDetailTab(id as PresetDetailTab)}
					ariaLabel="Preset details"
				/>

				{#if effectiveTab === 'overview'}
					<DetailBody>
						<DetailLayout>
							{#snippet lead()}
								{#if activeUnavailable && activeStateCopy}
									<div data-mirror-state-alert>
										<Alert variant="warning" density="compact" icon title={activeStateCopy.label}>
											{activeStateCopy.description}
											{#snippet actions()}
												<Button
													variant="secondary"
													size="xs"
													icon="trash"
													loading={mutatingPresetId === activePreset.id}
													disabled={mutatingPresetId !== null}
													onclick={() => handleUninstall(activePreset)}
												>Remove mirror</Button>
											{/snippet}
										</Alert>
									</div>
								{/if}
								{#if detailError}
									<Alert variant="warning" density="compact" live="polite">
										{detailError}. Catalog metadata is shown below.
										{#snippet actions()}
											<Button variant="ghost" size="xs" icon="refresh" onclick={() => loadPresetDetail(activePreset.id)}>Retry</Button>
										{/snippet}
									</Alert>
								{/if}
							{/snippet}

							{#snippet main()}
								<DetailSection label="Description">
									{#if descriptionHtml}
										<div class="text-sm leading-relaxed text-fg-muted">{@html descriptionHtml}</div>
									{:else}
										<p class="text-sm text-fg-subtle">No description has been provided for this preset yet.</p>
									{/if}
								</DetailSection>

								{#if activePreset.installed && !activeUnavailable}
									{#key activePreset.id}
										<PresetModesList presetId={activePreset.id} />
									{/key}
								{/if}

								<DetailSection label="Media">
									<div class="flex flex-col sm:flex-row gap-5">
										<button
											type="button"
											class="self-start rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-signal/40 {hasPresetMedia(activePreset) ? 'cursor-zoom-in' : 'cursor-default'}"
											onclick={() => hasPresetMedia(activePreset) && (mediaModalOpen = true)}
											aria-label={hasPresetMedia(activePreset) ? `View ${activePreset.name} media` : `${activePreset.name} has no media`}
										>
											<PresetThumbnail presetId={activePreset.id} presetName={activePreset.name} cover={activePreset.media?.cover} category={activePreset.category} size="w-28 h-28 sm:w-32 sm:h-32" variant="medium" />
										</button>

										<div class="min-w-0 flex-1">
											<div class="flex items-center gap-2 mb-2">
												<p class="text-xs font-medium text-fg-muted">Examples</p>
												{#if gallery.length}<span class="font-mono text-xs text-fg-subtle">{gallery.length}</span>{/if}
												{#if gallery.length > 6}<Button variant="ghost" size="xs" class="ml-auto" onclick={() => (mediaModalOpen = true)}>View all</Button>{/if}
											</div>
											{#if detailLoading}
												<div class="flex items-center justify-center py-12">
													<Spinner size="md" />
												</div>
											{:else if gallery.length}
												<div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
													{#each gallery.slice(0, 6) as item}
														<PresetExampleCard presetId={activePreset.id} presetName={activePreset.name} {item} onSelect={() => (mediaModalOpen = true)} />
													{/each}
												</div>
											{:else}
												<EmptyState title="No examples yet" description="Examples will appear here when they are included with the preset." icon={presetCategoryIcon(activePreset.category)} compact />
											{/if}
										</div>
									</div>
								</DetailSection>

								{#if activePreset.styles?.length}
									<DetailSection label="Styles">
										<div class="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 gap-3">
											{#each activePreset.styles as presetStyle}
												<div class="{DETAIL_INSET_CLASS} overflow-hidden">
													<div class="relative aspect-square w-full bg-surface-3">
														{#if presetStyle.preview}
															<img src={api.getPresetAssetURL(activePreset.id, presetStyle.preview, 'small')} alt={presetStyle.name} class="w-full h-full object-cover" loading="lazy" />
														{:else}
															<div class="flex h-full w-full items-center justify-center text-lg font-semibold text-fg-subtle">{presetStyle.name.charAt(0).toUpperCase()}</div>
														{/if}
													</div>
													<p class="px-2 py-1.5 truncate text-xs font-medium text-fg">{presetStyle.name}</p>
												</div>
											{/each}
										</div>
									</DetailSection>
								{/if}
							{/snippet}

							{#snippet aside()}
								{#if activePreset.is_mirror}
									<DetailSection label="Source">
										<div class="flex items-center justify-between gap-2" data-mirror-source-section>
											<div class="min-w-0">
												<p class="truncate text-sm text-fg">{activeSourceName ?? 'Missing preset'}</p>
												<p class="text-xs text-fg-subtle">Uses its files and gets its updates.</p>
											</div>
											{#if activeSourceAvailable && activePreset.source_preset_id}
												<Button variant="ghost" size="xs" icon="arrow-right" onclick={() => activePreset.source_preset_id && openPresetId(activePreset.source_preset_id)}>Open</Button>
											{/if}
										</div>
									</DetailSection>
								{:else if canCreateMirror}
									<DetailSection label="Mirrors">
										{#snippet headerExtra()}
											<Button variant="ghost" size="xs" icon="plus" onclick={openCreateMirror}>Create</Button>
										{/snippet}
										{#if activeMirrors.length}
											<ul class="-my-1 divide-y divide-line" data-preset-mirrors-list>
												{#each activeMirrors as mirror (mirror.preset_id)}
													{@const state = mirrorStateCopy(mirror.mirror_state)}
													<li class="flex items-center justify-between gap-2 py-1.5">
														<div class="flex min-w-0 items-center gap-2">
															<span class="truncate text-sm text-fg">{mirror.name}</span>
															{#if state}<Badge variant="warning" size="sm">{state.label}</Badge>{/if}
														</div>
														<Button variant="ghost" size="xs" onclick={() => openPresetId(mirror.preset_id)}>Open</Button>
													</li>
												{/each}
											</ul>
										{:else}
											<p class="text-sm text-fg-muted">No mirrors yet. A mirror is a separate preset with its own name, cover and defaults that uses these files.</p>
										{/if}
									</DetailSection>
								{/if}

								<DetailSection label="Metadata">
									<KVGrid>
										<KVItem label="ID" mono full>{activePreset.id}</KVItem>
										{#if activePreset.engine}<KVItem label="Engine">{activePreset.engine}</KVItem>{/if}
										{#if activePreset.version}<KVItem label="Version" mono>v{activePreset.version}</KVItem>{/if}
										{#if activePreset.file_name && activePreset.file_name !== activePreset.name}<KVItem label="File name">{activePreset.file_name}</KVItem>{/if}
										{#if activePreset.source}<KVItem label="Source">{activePreset.source}</KVItem>{/if}
										{#if activePreset.category}<KVItem label="Category">{presetCategoryLabel(activePreset.category)}</KVItem>{/if}
									</KVGrid>
									{#if activePreset.tags?.length}
										<div class="flex flex-wrap gap-1.5 mt-3">
											{#each activePreset.tags as tag}<Badge variant="neutral" size="sm">{tag}</Badge>{/each}
										</div>
									{/if}
								</DetailSection>

								{#if !activeUnavailable}
								<DetailSection label="Requirements">
									{#if requirementsBadge}
										<div class="flex items-center justify-between gap-2">
											<Badge variant={requirementsBadge.variant} size="sm">{requirementsBadge.label}</Badge>
											<Button variant="ghost" size="xs" onclick={() => selectDetailTab('requirements')}>View</Button>
										</div>
									{:else}
										<p class="text-sm text-fg-muted">Not checked yet.</p>
									{/if}
								</DetailSection>
								{/if}

								{#if presetRecipes.length}
									<DetailSection label="Recipe">
										<PresetRecipeSetup recipes={presetRecipes} session={recipeSession} />
									</DetailSection>
								{/if}
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{:else if effectiveTab === 'configuration'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								{#key activePreset.id}
									<PresetConfigurationTab
										bind:this={configurationTabRef}
										presetId={activePreset.id}
										initialEntries={presetConfigEntries}
										bind:dirtyCount={configDirtyCount}
										bind:saving={configSaving}
									/>
								{/key}
							{/snippet}
						</DetailLayout>
					</DetailBody>
					<DetailFooter
						dirtyCount={configDirtyCount}
						saving={configSaving}
						onSave={() => configurationTabRef?.save()}
						onDiscard={() => configurationTabRef?.discard()}
					/>
				{:else if effectiveTab === 'form'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								{#key activePreset.id}
									<PresetFormOverridesTab
										bind:this={formTabRef}
										presetId={activePreset.id}
										bind:dirtyCount={formDirtyCount}
										bind:saving={formSaving}
									/>
								{/key}
							{/snippet}
						</DetailLayout>
					</DetailBody>
					<DetailFooter
						dirtyCount={formDirtyCount}
						saving={formSaving}
						onSave={() => formTabRef?.save()}
						onDiscard={() => formTabRef?.discard()}
					/>
				{:else if effectiveTab === 'display'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								{#if activeAdminDetail}
									{#key activePreset.id}
										<PresetDisplayTab
											bind:this={displayTabRef}
											presetId={activePreset.id}
											initialDetail={activeAdminDetail}
											category={activePreset.category ?? null}
											bind:dirtyCount={displayDirtyCount}
											bind:saving={displaySaving}
											onChanged={handleDisplayChanged}
										/>
									{/key}
								{:else if adminDetailError}
									<EmptyState title="Display settings unavailable" description={adminDetailError} icon="warning" compact>
										{#snippet actions()}<Button size="sm" variant="secondary" icon="refresh" onclick={() => loadAdminDetail(activePreset.id)}>Try again</Button>{/snippet}
									</EmptyState>
								{:else}
									<div class="flex items-center justify-center py-10">
										<Spinner size="md" />
									</div>
								{/if}
							{/snippet}
						</DetailLayout>
					</DetailBody>
					<DetailFooter
						dirtyCount={displayDirtyCount}
						saving={displaySaving}
						onSave={() => displayTabRef?.save()}
						onDiscard={() => displayTabRef?.discard()}
					/>
				{:else if effectiveTab === 'prompt'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								{#key activePreset.id}
									<PresetPromptTextTab
										bind:this={promptTabRef}
										presetId={activePreset.id}
										vars={presetDetail?.id === activePreset.id ? (presetDetail.vars ?? null) : null}
										bind:dirtyCount={promptDirtyCount}
										bind:saving={promptSaving}
									/>
								{/key}
							{/snippet}
						</DetailLayout>
					</DetailBody>
					<DetailFooter
						dirtyCount={promptDirtyCount}
						saving={promptSaving}
						onSave={() => promptTabRef?.save()}
						onDiscard={() => promptTabRef?.discard()}
					/>
				{:else if effectiveTab === 'requirements'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								{#key activePreset.id}
									<PresetRequirementsTab presetId={activePreset.id} />
								{/key}
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{:else}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								{#if activePreset.installed}
									{#key activePreset.id}
										<AssignmentCard
											adapter={createPresetAssignmentAdapter(activePreset.id)}
											resourceKey={activePreset.id}
											resourceName={activePreset.name}
											on:changed={(event) => handleAccessChanged(activePreset.id, event)}
										/>
									{/key}
								{:else}
									<EmptyState title="Install before assigning access" description="Only installed presets can be made available to users and groups." icon="download">
										{#snippet actions()}
											<Button variant="secondary" icon="download" loading={mutatingPresetId === activePreset.id} onclick={() => handleInstall(activePreset)}>Install preset</Button>
										{/snippet}
									</EmptyState>
								{/if}
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{/if}
			</div>
		{/if}
	{:else}
		<div class="h-full overflow-y-auto p-4">
			{#if loading}
				<div class="flex h-40 items-center justify-center">
					<Spinner size="lg" />
				</div>
			{:else if loadError && presets.length === 0}
				<div class="flex h-full items-center justify-center">
					<EmptyState title="Preset catalog unavailable" description={loadError} icon="warning" compact>
						{#snippet actions()}<Button size="sm" variant="secondary" icon="refresh" onclick={() => loadPresets()}>Try again</Button>{/snippet}
					</EmptyState>
				</div>
			{:else if presets.length === 0}
				<div class="flex h-full items-center justify-center">
					<EmptyState title="No presets found" description="Add preset definitions to the catalog, then refresh this page." icon="cube" compact />
				</div>
			{:else if visiblePresets.length === 0}
				<div class="flex h-full items-center justify-center">
					<EmptyState title="No matching presets" description="Try a different name, category, engine, or installation status." icon="search" compact>
						{#snippet actions()}<Button size="sm" variant="secondary" onclick={() => updateFilters(presetFilters.clearAll(filters))}>Clear filters</Button>{/snippet}
					</EmptyState>
				</div>
			{:else}
				<div class="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3" role="list" aria-label="Preset catalog">
					{#each visiblePresets as preset (preset.id)}
						<PresetCard
							{preset}
							sourceName={mirrorSourceName(preset, presetsById)}
							installing={mutatingPresetId === preset.id}
							onOpen={openPreset}
							onInstall={handleInstall}
						/>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</LibraryShell>

<PresetMediaModal isOpen={mediaModalOpen} preset={activePreset} on:close={() => (mediaModalOpen = false)} />

<CreateMirrorModal bind:this={createMirrorModal} onCreated={handleMirrorCreated} />

<PresetHasMirrorsDialog
	isOpen={!!blockedUninstall}
	presetName={blockedUninstall?.name ?? ''}
	mirrors={blockedUninstall?.mirrors ?? []}
	onClose={() => (blockedUninstall = null)}
	onOpenMirror={openBlockingMirror}
/>
