<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { logger } from '$lib/utils/logger';
	import { availablePresetEngines, filterPresets } from '$lib/utils/presetFilter';
	import { hasPresetMedia } from '$lib/utils/presetMedia';
	import { processMarkdown } from '$lib/utils/markdown';
	import Icon from '$lib/components/Icon.svelte';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import { Pane, PaneRow, PaneGroupHeader } from '$lib/components/pane';
	import PresetThumbnail from '$lib/components/preset/PresetThumbnail.svelte';
	import PresetExampleCard from '$lib/components/preset/PresetExampleCard.svelte';
	import PresetMediaModal from '$lib/components/preset/PresetMediaModal.svelte';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import { createPresetAssignmentAdapter } from '$lib/components/assignment/presetAssignmentAdapter';
	import PresetConfigurationTab from './PresetConfigurationTab.svelte';
	import PresetFormOverridesTab from './PresetFormOverridesTab.svelte';
	import PresetDetailSubHeader from './PresetDetailSubHeader.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import { Badge, Button, EmptyState, Input, Spinner, Alert } from '$lib/components/ui';
	import type { PresetInfo, PresetConfigurationEntry } from '$lib/types/api';

	type InstallFilter = 'all' | 'installed' | 'not-installed';
	type DetailTab = 'overview' | 'access' | 'configuration' | 'form';

	let presets: PresetInfo[] = [];
	let loading = true;
	let refreshing = false;
	let loadError = '';
	let query = '';
	let selectedCategory: string | null = null;
	let selectedEngine: string | null = null;
	let installFilter: InstallFilter = 'all';
	let selectedPresetId = '';
	let detailTab: DetailTab = 'overview';
	let presetDetail: PresetInfo | null = null;
	let detailLoading = false;
	let detailError = '';
	let detailRequestVersion = 0;
	let mutatingPresetId: string | null = null;
	let mediaModalOpen = false;
	let presetConfigEntries: PresetConfigurationEntry[] = [];

	const primaryCategoryOrder = ['image', 'video', 'audio', '3d', 'utility'];

	$: engines = availablePresetEngines(presets);
	$: categories = Array.from(
		new Set(presets.map((preset) => preset.category).filter((category): category is string => !!category))
	).sort((a, b) => {
		const aIndex = primaryCategoryOrder.indexOf(a);
		const bIndex = primaryCategoryOrder.indexOf(b);
		return (aIndex === -1 ? 99 : aIndex) - (bIndex === -1 ? 99 : bIndex) || a.localeCompare(b);
	});
	$: categoryCounts = presets.reduce<Record<string, number>>((counts, preset) => {
		const category = preset.category || 'other';
		counts[category] = (counts[category] || 0) + 1;
		return counts;
	}, {});
	$: installedCount = presets.filter((preset) => preset.installed).length;
	$: uninstalledCount = presets.length - installedCount;
	$: assignedUserCount = presets.reduce((total, preset) => total + (preset.assignment_count || 0), 0);
	$: assignedGroupCount = presets.reduce((total, preset) => total + (preset.group_count || 0), 0);
	$: filteredPresets = filterPresets(presets, query, selectedEngine).filter((preset) => {
		if (selectedCategory && preset.category !== selectedCategory) return false;
		if (installFilter === 'installed' && !preset.installed) return false;
		if (installFilter === 'not-installed' && preset.installed) return false;
		return true;
	});
	$: groupedPresets = groupByCategory(filteredPresets);
	$: selectedPreset = presets.find((preset) => preset.id === selectedPresetId) || null;
	$: activePreset =
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
			: selectedPreset;
	$: descriptionHtml = activePreset?.description ? processMarkdown(activePreset.description) : '';
	$: gallery = activePreset?.media?.gallery || [];
	$: activeFilterCount = Number(!!query.trim()) + Number(!!selectedCategory) + Number(!!selectedEngine) + Number(installFilter !== 'all');

	$: if (!loading && !filteredPresets.some((preset) => preset.id === selectedPresetId)) {
		const nextId = filteredPresets[0]?.id || '';
		if (nextId !== selectedPresetId) selectPreset(nextId);
	}

	onMount(() => {
		loadPresets();
	});

	function categoryLabel(category: string) {
		if (category === 'image') return 'Image';
		if (category === 'video') return 'Video';
		if (category === 'audio') return 'Audio';
		if (category === '3d') return '3D';
		if (category === 'utility') return 'Utility';
		return category.charAt(0).toUpperCase() + category.slice(1);
	}

	function categoryIcon(category: string | null | undefined) {
		if (category === 'image') return 'photo';
		if (category === 'video') return 'film';
		if (category === 'audio') return 'audio';
		if (category === '3d') return 'cube';
		if (category === 'utility') return 'wand';
		return 'layers';
	}

	function groupByCategory(items: PresetInfo[]) {
		const groups = new Map<string, PresetInfo[]>();
		for (const preset of items) {
			const category = preset.category || 'other';
			groups.set(category, [...(groups.get(category) || []), preset]);
		}
		return [...groups.entries()]
			.sort(([a], [b]) => {
				const aIndex = primaryCategoryOrder.indexOf(a);
				const bIndex = primaryCategoryOrder.indexOf(b);
				return (aIndex === -1 ? 99 : aIndex) - (bIndex === -1 ? 99 : bIndex) || a.localeCompare(b);
			})
			.map(([category, categoryPresets]) => ({ category, presets: categoryPresets }));
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

	function selectPreset(id: string) {
		selectedPresetId = id;
		detailTab = 'overview';
		presetDetail = null;
		detailError = '';
		mediaModalOpen = false;
		presetConfigEntries = [];
		if (id) {
			loadPresetDetail(id);
			if (presets.find((preset) => preset.id === id)?.installed) loadPresetConfigEntries(id);
		}
	}

	async function loadPresetConfigEntries(id: string) {
		try {
			const response = await api.getPresetConfiguration(id);
			if (response.success && response.data && id === selectedPresetId) {
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
			if (version !== detailRequestVersion || id !== selectedPresetId) return;
			presetDetail = response.data;
		} catch (error) {
			if (version !== detailRequestVersion || id !== selectedPresetId) return;
			logger.error('Failed to load preset details:', error);
			detailError = error instanceof Error ? error.message : 'Could not load preset details';
		} finally {
			if (version === detailRequestVersion && id === selectedPresetId) detailLoading = false;
		}
	}

	function clearFilters() {
		query = '';
		selectedCategory = null;
		selectedEngine = null;
		installFilter = 'all';
	}

	async function handleInstall(preset: PresetInfo) {
		mutatingPresetId = preset.id;
		try {
			const response = await adminApi.installPreset(preset.id);
			if (!response.success) {
				throw new Error(responseError(response, 'The preset could not be installed'));
			}
			toasts.success(`${preset.name} installed`);
			await loadPresets(true);
			detailTab = 'access';
			loadPresetConfigEntries(preset.id);
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
			await loadPresets(true);
		} catch (error) {
			logger.error('Failed to uninstall preset:', error);
			toasts.error(error instanceof Error ? error.message : 'Failed to uninstall preset');
		} finally {
			mutatingPresetId = null;
		}
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

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="Preset Management"
		icon="cube"
		counts={[
			{ label: 'catalog', value: presets.length },
			{ label: 'installed', value: installedCount, tone: 'success' },
			{ label: 'assignments', value: assignedUserCount + assignedGroupCount, tone: 'info' }
		]}
	>
		{#snippet actions()}
			<Button variant="secondary" size="sm" icon="refresh" loading={refreshing} onclick={() => loadPresets(true)}>Refresh catalog</Button>
		{/snippet}
	</AdminTabShell>

	{#snippet presetSearch()}
		<div class="relative">
			<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
			<Input bind:value={query} type="search" class="pl-9" placeholder="Search presets by name, engine, type, or tag…" aria-label="Search presets" />
		</div>
	{/snippet}
	<!-- Selects use a plain label + aria-label rather than id/for — AdminFilterBar
	     renders `filters` twice (inline at lg+, again inside the below-lg
	     popover), and duplicate ids would break label association whenever the
	     popover is open on a narrow screen. This is the collapse stress test:
	     three selects, only shown inline once there's room for them. -->
	{#snippet presetFilters()}
		<div class="flex items-center gap-2">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Type</span>
			<select class="input w-40" bind:value={selectedCategory} aria-label="Filter by type">
				<option value={null}>All types</option>
				{#each categories as category}
					<option value={category}>{categoryLabel(category)} ({categoryCounts[category] || 0})</option>
				{/each}
			</select>
		</div>

		{#if engines.length}
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Engine</span>
				<select class="input w-40" bind:value={selectedEngine} aria-label="Filter by engine">
					<option value={null}>All engines</option>
					{#each engines as engine}<option value={engine}>{engine}</option>{/each}
				</select>
			</div>
		{/if}

		<div class="flex items-center gap-2">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Status</span>
			<select class="input w-40" bind:value={installFilter} aria-label="Filter by status">
				<option value="all">All ({presets.length})</option>
				<option value="installed">Installed ({installedCount})</option>
				<option value="not-installed">Not installed ({uninstalledCount})</option>
			</select>
		</div>
	{/snippet}
	{#snippet presetFiltersTrailing()}
		<span class="text-sm text-fg-muted whitespace-nowrap font-mono tabular-nums">{filteredPresets.length} {filteredPresets.length === 1 ? 'preset' : 'presets'}</span>
	{/snippet}

	<AdminFilterBar
		search={presetSearch}
		filters={presetFilters}
		trailing={presetFiltersTrailing}
		activeCount={activeFilterCount}
		onClear={clearFilters}
	/>

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		{#if loading}
			<div class="h-full flex flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading preset catalog…</p>
			</div>
		{:else if loadError && presets.length === 0}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState title="Preset catalog unavailable" description={loadError} icon="warning" compact>
					{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={() => loadPresets()}>Try again</Button>{/snippet}
				</EmptyState>
			</div>
		{:else if presets.length === 0}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState title="No presets found" description="Add preset definitions to the catalog, then refresh this page." icon="cube" compact />
			</div>
		{:else}
			<MasterDetailLayout leftWidth={360} minWidth={300} maxWidth={480} storageKey="admin-preset-catalog-width">
				<div slot="list" class="h-full min-h-0">
					<Pane
						label="Browse presets"
						count={filteredPresets.length}
						isEmpty={filteredPresets.length === 0}
						bodyRole="listbox"
						ariaLabel="Preset catalog"
					>
						{#snippet empty()}
							<div class="p-4 h-full flex items-center justify-center">
								<EmptyState title="No matching presets" description="Try a different name, type, engine, or installation status." icon="search" compact>
									{#snippet actions()}<Button variant="ghost" size="sm" onclick={clearFilters}>Clear filters</Button>{/snippet}
								</EmptyState>
							</div>
						{/snippet}

						{#snippet children()}
							{#each groupedPresets as group (group.category)}
								<PaneGroupHeader
									icon={categoryIcon(group.category)}
									label={categoryLabel(group.category)}
									count={group.presets.length}
								/>

								{#each group.presets as preset (preset.id)}
									{#snippet presetLeading()}
										<PresetThumbnail presetId={preset.id} presetName={preset.name} cover={preset.media?.cover} category={preset.category} size="w-12 h-12" />
									{/snippet}
									{#snippet presetMeta()}
										{#if preset.installed}
											<p class="flex items-center gap-2 text-2xs text-fg-muted mt-1">
												<span class="inline-flex items-center gap-1"><Icon name="user" className="w-3 h-3" />{preset.assignment_count || 0}</span>
												<span class="inline-flex items-center gap-1"><Icon name="group" className="w-3 h-3" />{preset.group_count || 0}</span>
												{#if !(preset.assignment_count || 0) && !(preset.group_count || 0)}
													<span title="Only admins can see this — assign users or groups">
														<Badge variant="warning" size="sm">Unassigned</Badge>
													</span>
												{/if}
											</p>
										{/if}
									{/snippet}
									{#snippet presetTrailing()}
										<Badge variant={preset.installed ? 'success' : 'neutral'} size="sm" dot={!!preset.installed}>{preset.installed ? 'installed' : 'available'}</Badge>
									{/snippet}
									<PaneRow
										selected={selectedPresetId === preset.id}
										onclick={() => selectPreset(preset.id)}
										leading={presetLeading}
										title={preset.name}
										subtitle="{preset.engine || 'unknown engine'} · v{preset.version}"
										subtitleMono
										meta={presetMeta}
										trailing={presetTrailing}
									/>
								{/each}
							{/each}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					{#if activePreset}
						<div class="flex flex-wrap items-center gap-2 px-4 sm:px-5 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
							<nav class="inline-flex items-center gap-1" aria-label="Preset details">
								<button
									type="button"
									class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'overview' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
									on:click={() => (detailTab = 'overview')}
									aria-current={detailTab === 'overview' ? 'page' : undefined}
								><Icon name="info" className="w-3.5 h-3.5" />Overview</button>
								<button
									type="button"
									class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'access' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
									on:click={() => (detailTab = 'access')}
									aria-current={detailTab === 'access' ? 'page' : undefined}
								>
									<Icon name="group" className="w-3.5 h-3.5" />Access
									{#if activePreset.installed}<span class="font-mono text-2xs opacity-70">{(activePreset.assignment_count || 0) + (activePreset.group_count || 0)}</span>{/if}
								</button>
								{#if activePreset.installed && presetConfigEntries.length}
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'configuration' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (detailTab = 'configuration')}
										aria-current={detailTab === 'configuration' ? 'page' : undefined}
									><Icon name="sliders" className="w-3.5 h-3.5" />Configuration</button>
								{/if}
								{#if activePreset.installed}
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {detailTab === 'form' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (detailTab = 'form')}
										aria-current={detailTab === 'form' ? 'page' : undefined}
									><Icon name="document" className="w-3.5 h-3.5" />Form</button>
								{/if}
							</nav>

							<div class="ml-auto flex items-center gap-2">
								{#if activePreset.installed}
									<Badge variant="success" dot>Installed</Badge>
									<Button
										variant="ghost"
										size="sm"
										class="text-danger hover:text-danger hover:bg-danger/10"
										loading={mutatingPresetId === activePreset.id}
										disabled={mutatingPresetId !== null}
										onclick={() => handleUninstall(activePreset)}
									>Uninstall</Button>
								{:else}
									<Badge variant="neutral">Not installed</Badge>
									<Button
										variant="primary"
										size="sm"
										icon="download"
										loading={mutatingPresetId === activePreset.id}
										disabled={mutatingPresetId !== null}
										onclick={() => handleInstall(activePreset)}
									>Install preset</Button>
								{/if}
							</div>
						</div>

						<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2">
							{#if detailTab === 'overview'}
								<div class="p-5 sm:p-7 space-y-7">
									<div class="flex flex-col md:flex-row gap-5 md:gap-7">
										<button
											type="button"
											class="self-start rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 {hasPresetMedia(activePreset) ? 'cursor-zoom-in' : 'cursor-default'}"
											on:click={() => hasPresetMedia(activePreset) && (mediaModalOpen = true)}
											aria-label={hasPresetMedia(activePreset) ? `View ${activePreset.name} media` : `${activePreset.name} has no media`}
										>
											<PresetThumbnail presetId={activePreset.id} presetName={activePreset.name} cover={activePreset.media?.cover} category={activePreset.category} size="w-40 h-40 sm:w-48 sm:h-48" variant="large" />
										</button>

										<div class="min-w-0 flex-1 pt-1">
											<p class="label mb-1.5 inline-flex items-center gap-1.5"><Icon name={categoryIcon(activePreset.category)} className="w-3.5 h-3.5" />{categoryLabel(activePreset.category || 'preset')}</p>
											<h2 class="text-2xl font-semibold text-fg leading-tight">{activePreset.name}</h2>
											<div class="flex flex-wrap items-center gap-2 mt-3">
												{#if activePreset.engine}<Badge variant="info">{activePreset.engine}</Badge>{/if}
												<Badge variant="neutral">v{activePreset.version}</Badge>
												{#if activePreset.source}<Badge variant="neutral">{activePreset.source}</Badge>{/if}
											</div>
											<p class="font-mono text-2xs text-fg-subtle mt-4 break-all">{activePreset.id}</p>
											{#if activePreset.tags?.length}
												<div class="flex flex-wrap gap-1.5 mt-4">
													{#each activePreset.tags as tag}<Badge variant="neutral" size="sm">{tag}</Badge>{/each}
												</div>
											{/if}
										</div>
									</div>

									{#if detailError}
										<Alert variant="warning" density="compact" live="polite">
											{detailError}. Catalog metadata is shown below.
											{#snippet actions()}
												<Button variant="ghost" size="xs" icon="refresh" onclick={() => loadPresetDetail(activePreset.id)}>Retry</Button>
											{/snippet}
										</Alert>
									{/if}

									<section>
										<div class="flex items-center gap-2 mb-3">
											<div class="w-7 h-7 rounded bg-surface-1 border border-line flex items-center justify-center text-fg-muted"><Icon name="document" className="w-3.5 h-3.5" /></div>
											<h3 class="text-sm font-semibold text-fg">About this preset</h3>
										</div>
										<div class="rounded-lg border border-line bg-surface-1 p-4 sm:p-5">
											{#if descriptionHtml}
												<div class="text-sm leading-relaxed text-fg-muted">{@html descriptionHtml}</div>
											{:else}
												<p class="text-sm text-fg-subtle">No description has been provided for this preset yet.</p>
											{/if}
										</div>
									</section>

									<section>
										<div class="flex items-center gap-2 mb-3">
											<div class="w-7 h-7 rounded bg-surface-1 border border-line flex items-center justify-center text-fg-muted"><Icon name="photo" className="w-3.5 h-3.5" /></div>
											<h3 class="text-sm font-semibold text-fg">Examples</h3>
											{#if gallery.length}<span class="font-mono text-2xs text-fg-subtle">{gallery.length}</span>{/if}
											{#if gallery.length > 6}<Button variant="ghost" size="xs" class="ml-auto" onclick={() => (mediaModalOpen = true)}>View all</Button>{/if}
										</div>
										{#if detailLoading}
											<div class="rounded-lg border border-line bg-surface-1 py-12 flex flex-col items-center justify-center">
												<Spinner size="md" />
												<p class="text-sm text-fg-muted mt-3">Loading examples…</p>
											</div>
										{:else if gallery.length}
											<div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
												{#each gallery.slice(0, 6) as item}
													<PresetExampleCard presetId={activePreset.id} presetName={activePreset.name} {item} onSelect={() => (mediaModalOpen = true)} />
												{/each}
											</div>
										{:else}
											<div class="rounded-lg border border-dashed border-line-strong bg-surface-1/60 px-5 py-8 text-center">
												<Icon name={categoryIcon(activePreset.category)} className="w-7 h-7 text-fg-subtle mx-auto mb-2" />
												<p class="text-sm text-fg-muted">Examples will appear here when they are included with the preset.</p>
											</div>
										{/if}
									</section>
								</div>
							{:else if detailTab === 'configuration'}
								<div class="p-5 sm:p-7">
									<PresetDetailSubHeader
										icon="sliders"
										title="Configuration for {activePreset.name}"
										description="Preset-declared configuration that isn't part of the user-facing generation form."
									/>
								{#key activePreset.id}
									<PresetConfigurationTab presetId={activePreset.id} initialEntries={presetConfigEntries} />
								{/key}
								</div>
							{:else if detailTab === 'form'}
								<div class="p-5 sm:p-7">
									<PresetDetailSubHeader
										icon="document"
										title="Form for {activePreset.name}"
										description="Set per-field defaults, editability, and visibility for the user-facing generation form."
									/>
								{#key activePreset.id}
									<PresetFormOverridesTab presetId={activePreset.id} />
								{/key}
								</div>
							{:else}
								<div class="p-5 sm:p-7">
									<PresetDetailSubHeader
										icon="shield"
										title="Access to {activePreset.name}"
										description="Assign the preset directly to specific users or grant it to every member of a user group."
									/>

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
										<div class="rounded-xl border border-line bg-surface-1 p-7 text-center">
											<div class="w-12 h-12 rounded-full bg-surface-3 text-fg-muted flex items-center justify-center mx-auto mb-3"><Icon name="download" className="w-5 h-5" /></div>
											<h3 class="text-sm font-semibold text-fg">Install before assigning access</h3>
											<p class="text-sm text-fg-muted mt-1 max-w-md mx-auto">Only installed presets can be made available to users and groups.</p>
											<Button variant="primary" size="sm" icon="download" class="mt-4" loading={mutatingPresetId === activePreset.id} onclick={() => handleInstall(activePreset)}>Install preset</Button>
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{:else}
						<div class="h-full p-5 flex items-center justify-center">
							<EmptyState title="No preset selected" description="Choose a preset from the catalog to inspect its details." icon="cube" compact />
						</div>
					{/if}
				</div>
			</MasterDetailLayout>
		{/if}
	</section>
</div>

<PresetMediaModal isOpen={mediaModalOpen} preset={activePreset} on:close={() => (mediaModalOpen = false)} />
