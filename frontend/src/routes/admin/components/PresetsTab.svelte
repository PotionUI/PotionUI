<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
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
	import PresetFiltersPopover from './presets/PresetFiltersPopover.svelte';
	import { PRESET_LIBRARY_SECTIONS, type PresetLibrarySection } from './presets/presetLibrarySections';
	import {
		PRESET_SORT_OPTIONS,
		applyPresetFilters,
		clearAllPresetFilters,
		clearPresetFilterChip,
		presetEngineCounts,
		presetEngines,
		presetFilterActiveCount,
		presetFilterChips,
		presetFiltersFromSearchParams,
		presetFiltersToSearchParams,
		presetSectionCounts,
		type PresetFilters,
		type PresetSortBy
	} from './presets/presetFilters';
	import { Badge, Button, EmptyState, Spinner, Alert, IconButton } from '$lib/components/ui';
	import type { PresetInfo, PresetConfigurationEntry, PresetRecipeLink } from '$lib/types/api';

	type DetailTab = 'overview' | 'access' | 'configuration' | 'form' | 'requirements';

	let presets = $state<PresetInfo[]>([]);
	let loading = $state(true);
	let refreshing = $state(false);
	let loadError = $state('');
	let detailTab = $state<DetailTab>('overview');
	let presetDetail = $state<PresetInfo | null>(null);
	let detailLoading = $state(false);
	let detailError = $state('');
	let mutatingPresetId = $state<string | null>(null);
	let mediaModalOpen = $state(false);
	let presetConfigEntries = $state<PresetConfigurationEntry[]>([]);
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

	const section = $derived(($page.url.searchParams.get('section') as PresetLibrarySection) || 'all');
	const filters = $derived(presetFiltersFromSearchParams($page.url.searchParams));
	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);

	const engines = $derived(presetEngines(presets));
	const engineCounts = $derived(presetEngineCounts(presets));
	const sectionCounts = $derived(presetSectionCounts(presets));
	const visiblePresets = $derived(applyPresetFilters(presets, filters, section));
	const chips = $derived(presetFilterChips(filters));
	const activeFilterCount = $derived(presetFilterActiveCount(filters));

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
	const headerChips = $derived(
		activePreset
			? [
					...(activePreset.engine ? [{ key: 'engine', label: activePreset.engine, tone: 'signal' as const }] : []),
					{ key: 'version', label: `v${activePreset.version}`, tone: 'neutral' as const },
					activePreset.installed
						? { key: 'installed', label: 'Installed', tone: 'success' as const }
						: { key: 'installed', label: 'Not installed', tone: 'neutral' as const }
				]
			: []
	);

	const recipeSession = new RecipeRunSession({
		onFinished: () => {
			invalidatePresets();
			void loadPresets(true);
			if (viewId) loadPresetDetail(viewId);
		}
	});

	onDestroy(() => recipeSession.dispose());

	function startRecipe(recipe: PresetRecipeLink) {
		detailTab = 'overview';
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
			detailTab = 'overview';
			recipeSession.reset();
			presetDetail = null;
			detailError = '';
			mediaModalOpen = false;
			presetConfigEntries = [];
			headerOverflowOpen = false;
			configDirtyCount = 0;
			formDirtyCount = 0;
			if (viewId) {
				loadPresetDetail(viewId);
				if (presets.find((preset) => preset.id === viewId)?.installed) loadPresetConfigEntries(viewId);
			}
		});
	});

	function detailTabsFor(preset: PresetInfo) {
		const tabs: { id: DetailTab; label: string; icon: string; count?: number }[] = [
			{ id: 'overview', label: 'Overview', icon: 'info' },
			{
				id: 'access',
				label: 'Access',
				icon: 'group',
				count: preset.installed ? (preset.assignment_count || 0) + (preset.group_count || 0) : undefined
			}
		];
		if (preset.installed && presetConfigEntries.length) {
			tabs.push({ id: 'configuration', label: 'Configuration', icon: 'sliders' });
		}
		if (preset.installed) tabs.push({ id: 'form', label: 'Form', icon: 'document' });
		const missing = preset.requirements_summary?.missing ?? 0;
		tabs.push({ id: 'requirements', label: 'Requirements', icon: 'check', count: missing > 0 ? missing : undefined });
		return tabs;
	}

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

	function buildUrl(overrides: { section?: PresetLibrarySection; id?: string | null; filters?: PresetFilters } = {}): string {
		const params = presetFiltersToSearchParams(overrides.filters ?? filters);
		params.set('tab', 'presets');
		const nextSection = overrides.section ?? section;
		if (nextSection !== 'all') params.set('section', nextSection);
		const id = overrides.id !== undefined ? overrides.id : viewId;
		if (id) params.set('id', id);
		const query = params.toString();
		return query ? `${$page.url.pathname}?${query}` : $page.url.pathname;
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;

	function updateFilters(next: PresetFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ filters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function selectSection(id: PresetLibrarySection) {
		void goto(buildUrl({ section: id, id: null }));
	}

	function toggleEngineFilter(engine: string) {
		const nextEngine = filters.engine === engine ? '' : engine;
		void goto(buildUrl({ id: null, filters: { ...filters, engine: nextEngine } }));
	}

	function openPresetId(id: string) {
		void goto(buildUrl({ id }));
	}

	function openPreset(preset: PresetInfo) {
		openPresetId(preset.id);
	}

	function backToGrid() {
		void goto(buildUrl({ id: null }));
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
				detailTab = 'access';
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
		if (
			!(await confirmDialog({
				title: `Uninstall “${preset.name}”?`,
				message: assignmentWarning.trim(),
				variant: 'danger'
			}))
		)
			return;

		mutatingPresetId = preset.id;
		try {
			const response = await adminApi.uninstallPreset(preset.id);
			if (!response.success) {
				throw new Error(responseError(response, 'The preset could not be uninstalled'));
			}
			toasts.success(`${preset.name} uninstalled`);
			detailTab = 'overview';
			invalidatePresets();
			await loadPresets(true);
		} catch (error) {
			logger.error('Failed to uninstall preset:', error);
			toasts.error(error instanceof Error ? error.message : 'Failed to uninstall preset');
		} finally {
			mutatingPresetId = null;
		}
	}

	function closeHeaderOverflow() {
		headerOverflowOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		if (headerOverflowOpen && headerOverflowEl && !headerOverflowEl.contains(event.target as Node)) closeHeaderOverflow();
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') closeHeaderOverflow();
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

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

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
	onRemoveChip={(key) => updateFilters(clearPresetFilterChip(filters, key))}
	onClearFilters={() => updateFilters(clearAllPresetFilters(filters))}
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
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={backToGrid}>Back to presets</Button>{/snippet}
					</EmptyState>
				{/if}
			</div>
		{:else}
			<div class="flex h-full flex-col">
				<DetailHeader title={activePreset.name} icon={presetCategoryIcon(activePreset.category)} backLabel="Presets" onBack={backToGrid} chipItems={headerChips}>
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
									>
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
											Uninstall
										</button>
									</div>
								{/if}
							</div>
						{/if}
					{/snippet}
				</DetailHeader>

				<DetailTabs
					tabs={detailTabsFor(activePreset)}
					active={detailTab}
					onSelect={(id) => (detailTab = id as DetailTab)}
					ariaLabel="Preset details"
				/>

				{#if detailTab === 'overview'}
					<DetailBody>
						<DetailLayout>
							{#snippet lead()}
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

								<DetailSection label="Media">
									<div class="flex flex-col sm:flex-row gap-5">
										<button
											type="button"
											class="self-start rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 {hasPresetMedia(activePreset) ? 'cursor-zoom-in' : 'cursor-default'}"
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
								<DetailSection label="Metadata">
									<KVGrid>
										<KVItem label="ID" mono full>{activePreset.id}</KVItem>
										{#if activePreset.engine}<KVItem label="Engine">{activePreset.engine}</KVItem>{/if}
										<KVItem label="Version" mono>v{activePreset.version}</KVItem>
										{#if activePreset.source}<KVItem label="Source">{activePreset.source}</KVItem>{/if}
										{#if activePreset.category}<KVItem label="Category">{presetCategoryLabel(activePreset.category)}</KVItem>{/if}
									</KVGrid>
									{#if activePreset.tags?.length}
										<div class="flex flex-wrap gap-1.5 mt-3">
											{#each activePreset.tags as tag}<Badge variant="neutral" size="sm">{tag}</Badge>{/each}
										</div>
									{/if}
								</DetailSection>

								<DetailSection label="Requirements">
									{#if requirementsBadge}
										<div class="flex items-center justify-between gap-2">
											<Badge variant={requirementsBadge.variant} size="sm">{requirementsBadge.label}</Badge>
											<Button variant="ghost" size="xs" onclick={() => (detailTab = 'requirements')}>View</Button>
										</div>
									{:else}
										<p class="text-sm text-fg-muted">Not checked yet.</p>
									{/if}
								</DetailSection>

								{#if presetRecipes.length}
									<DetailSection label="Recipe">
										<PresetRecipeSetup recipes={presetRecipes} session={recipeSession} />
									</DetailSection>
								{/if}
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{:else if detailTab === 'configuration'}
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
				{:else if detailTab === 'form'}
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
				{:else if detailTab === 'requirements'}
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
											<Button variant="primary" size="sm" icon="download" loading={mutatingPresetId === activePreset.id} onclick={() => handleInstall(activePreset)}>Install preset</Button>
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
						{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={() => loadPresets()}>Try again</Button>{/snippet}
					</EmptyState>
				</div>
			{:else if presets.length === 0}
				<div class="flex h-full items-center justify-center">
					<EmptyState title="No presets found" description="Add preset definitions to the catalog, then refresh this page." icon="cube" compact />
				</div>
			{:else if visiblePresets.length === 0}
				<div class="flex h-full items-center justify-center">
					<EmptyState title="No matching presets" description="Try a different name, category, engine, or installation status." icon="search" compact>
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => updateFilters(clearAllPresetFilters(filters))}>Clear filters</Button>{/snippet}
					</EmptyState>
				</div>
			{:else}
				<div class="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3" role="list" aria-label="Preset catalog">
					{#each visiblePresets as preset (preset.id)}
						<PresetCard {preset} installing={mutatingPresetId === preset.id} onOpen={openPreset} onInstall={handleInstall} />
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</LibraryShell>

<PresetMediaModal isOpen={mediaModalOpen} preset={activePreset} on:close={() => (mediaModalOpen = false)} />
