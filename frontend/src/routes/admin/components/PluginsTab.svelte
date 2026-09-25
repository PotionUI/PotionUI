<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { parseServerDate } from '$lib/utils/relativeTime';
	import { pluginStore, plugins, frontendHooks, loading, error, pendingPluginIds, type Plugin, type PluginSettingSchema } from '$lib/stores/plugins';
	import { authStore } from '$lib/stores/auth';
	import { Button, Badge, Spinner, Input, EmptyState, LoadErrorState, Switch, Alert } from '$lib/components/ui';
	import {
		DetailHeader,
		DetailTabs,
		DetailBody,
		DetailLayout,
		DetailSection,
		DetailFooter,
		KVGrid,
		KVItem,
		DETAIL_INSET_CLASS
	} from '$lib/components/detail';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryDensityToggle from '$lib/components/library/LibraryDensityToggle.svelte';
	import { libraryCardDensity } from '$lib/components/library/libraryCardDensity';
	import Icon from '$lib/components/Icon.svelte';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import { refreshPluginExtensions } from '$lib/plugin-api/extensionRefresh';
	import { pluginDetailTabsFor, isPluginDetailTab, hasHiddenAdminTabs, ADMIN_PLUGIN_TABS_HOOK, type PluginDetailTabId } from './pluginDetailTabs';
	import { resolveCategory } from '$lib/plugins/categories';
	import { PLUGIN_SECTIONS, pluginSectionFromSearchParams, type PluginSection } from './plugins/pluginSections';
	import PluginFiltersPopover from './plugins/PluginFiltersPopover.svelte';
	import PluginCard from './plugins/PluginCard.svelte';
	import {
		PLUGIN_SORT_OPTIONS,
		applyPluginFilters,
		clearAllPluginFilters,
		clearPluginFilterChip,
		pluginCategoryCounts,
		pluginFilterActiveCount,
		pluginFilterChips,
		pluginFiltersFromSearchParams,
		pluginFiltersToSearchParams,
		type PluginFilters
	} from './plugins/pluginFilters';


	let detailTab = $state<PluginDetailTabId>('overview');
	let selectedPlugin = $state<Plugin | null>(null);
	let detailLoading = $state(false);
	let loadedDetailId = $state<string | null>(null);
	let settingsValues = $state<Record<string, any>>({});
	let settingsSnapshot = $state('{}');
	let saving = $state(false);
	let scanning = $state(false);
	let scanResult = $state<{ newPlugins: number; updatedPlugins: number } | null>(null);

	const params = $derived($page.url.searchParams);
	const section = $derived(pluginSectionFromSearchParams(params));
	const filters = $derived(pluginFiltersFromSearchParams(params));
	const selectedPluginId = $derived(params.get('id'));
	const detailOpen = $derived(!!selectedPluginId);

	const visiblePlugins = $derived(applyPluginFilters($plugins, section, filters));
	const sectionCounts = $derived(pluginCategoryCounts($plugins));
	const chips = $derived(pluginFilterChips(filters));
	const filterCount = $derived(pluginFilterActiveCount(filters));

	const listMatch = $derived(selectedPluginId ? $plugins.find((p) => p.id === selectedPluginId) : undefined);
	const liveSelected = $derived(
		selectedPlugin
			? {
					...selectedPlugin,
					enabled: listMatch?.enabled ?? selectedPlugin.enabled,
					state: listMatch?.state ?? selectedPlugin.state,
					error: listMatch?.error ?? selectedPlugin.error
				}
			: null
	);

	const adminTabHooks = $derived($frontendHooks[ADMIN_PLUGIN_TABS_HOOK] ?? []);
	const detailTabs = $derived(liveSelected ? pluginDetailTabsFor(liveSelected, adminTabHooks, $authStore.user?.account_type) : []);
	const showHiddenAdminTabsHint = $derived(liveSelected ? hasHiddenAdminTabs(liveSelected.hooks, liveSelected.enabled) : false);
	const pluginIcon = $derived(liveSelected ? resolveCategory(liveSelected.category).icon : undefined);
	const pluginChips = $derived(
		liveSelected
			? [
					...(liveSelected.shadows ? [{ key: 'shadows', label: 'SHADOWS MARKETPLACE COPY', tone: 'warning' as const }] : []),
					...(liveSelected.state === 'error' ? [{ key: 'error', label: 'ERROR', tone: 'danger' as const }] : []),
					{ key: 'version', label: `v${liveSelected.version}`, tone: 'neutral' as const },
					{ key: 'type', label: liveSelected.type.toUpperCase(), tone: 'neutral' as const },
					...(liveSelected.source ? [{ key: 'source', label: liveSelected.source.toUpperCase(), tone: 'neutral' as const }] : [])
				]
			: []
	);
	const settingsDirtyKeys = $derived.by(() => {
		const before = JSON.parse(settingsSnapshot) as Record<string, any>;
		const keys = new Set([...Object.keys(before), ...Object.keys(settingsValues)]);
		return [...keys].filter((k) => JSON.stringify(settingsValues[k]) !== JSON.stringify(before[k]));
	});
	const activeContributedTab = $derived(detailTabs.find((t) => t.id === detailTab && t.componentPath));
	const activeTabComponentPromise = $derived(
		activeContributedTab && liveSelected
			? resolvePluginComponent(liveSelected.id, activeContributedTab.componentPath as string)
			: null
	);

	onMount(async () => {
		await Promise.all([pluginStore.loadPlugins(), refreshPluginExtensions()]);
		window.addEventListener('potionui:switch-plugin-tab', handleSwitchPluginTab as EventListener);
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('potionui:switch-plugin-tab', handleSwitchPluginTab as EventListener);
		}
	});

	function handleSwitchPluginTab(e: CustomEvent<{ pluginId?: string; tabId?: string }>) {
		const { pluginId, tabId } = e.detail || {};
		if (!liveSelected || !tabId || pluginId !== liveSelected.id) return;
		if (!isPluginDetailTab(liveSelected, adminTabHooks, tabId, $authStore.user?.account_type)) return;
		detailTab = tabId;
	}

	async function scanForPlugins() {
		scanning = true;
		scanResult = null;
		const result = await pluginStore.scanPlugins();
		scanning = false;
		if (result) {
			await refreshPluginExtensions();
			if (selectedPluginId) {
				const refreshed = await pluginStore.getPluginDetails(selectedPluginId);
				if (refreshed) {
					selectedPlugin = refreshed;
					initSettingsValues(refreshed);
				}
			}
			scanResult = result;
			setTimeout(() => {
				scanResult = null;
			}, 5000);
		}
	}

	async function togglePlugin(plugin: Plugin) {
		if (await pluginStore.togglePlugin(plugin.id, !plugin.enabled)) {
			await refreshPluginExtensions();
		}
	}

	function initSettingsValues(detail: Plugin) {
		const schema = detail.settings_schema || [];
		const values: Record<string, any> = { ...(detail.settings_values || {}) };
		for (const s of schema) {
			if (!(s.name in values) && s.default !== undefined) values[s.name] = s.default;
		}
		settingsValues = values;
		settingsSnapshot = JSON.stringify(values);
	}

	async function saveSettings() {
		if (!selectedPlugin) return;
		saving = true;
		const success = await pluginStore.updatePluginSettings(selectedPlugin.id, settingsValues);
		saving = false;
		if (success) {
			const pluginDetails = await pluginStore.getPluginDetails(selectedPlugin.id);
			if (pluginDetails) {
				selectedPlugin = pluginDetails;
				initSettingsValues(pluginDetails);
			}
		}
	}

	function resetSettings() {
		if (!selectedPlugin) return;
		initSettingsValues(selectedPlugin);
	}

	function getInputType(schema: PluginSettingSchema): string {
		if (schema.is_secret) return 'password';
		if (schema.type === 'number') return 'number';
		return 'text';
	}

	function buildUrl(overrides: { filters?: PluginFilters; section?: PluginSection; id?: string | null } = {}): string {
		const nextFilters = overrides.filters ?? filters;
		const next = pluginFiltersToSearchParams(nextFilters);
		next.set('tab', 'plugins');
		const nextSection = overrides.section !== undefined ? overrides.section : section;
		if (nextSection !== 'all') next.set('category', nextSection);
		const id = overrides.id !== undefined ? overrides.id : selectedPluginId;
		if (id) next.set('id', id);
		return `/admin?${next.toString()}`;
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;
	function updateFilters(next: PluginFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ filters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function selectSection(id: PluginSection) {
		void goto(buildUrl({ section: id, id: null }), { keepFocus: true, noScroll: true });
	}

	function openPlugin(plugin: Plugin) {
		void goto(buildUrl({ id: plugin.id }), { noScroll: true });
	}

	function backToList() {
		void goto(buildUrl({ id: null }), { noScroll: true });
	}

	async function loadDetail(id: string) {
		detailTab = 'overview';
		detailLoading = true;
		const detail = await pluginStore.getPluginDetails(id);
		detailLoading = false;
		if (!detail) return;
		selectedPlugin = detail;
		initSettingsValues(detail);
	}

	$effect(() => {
		const id = selectedPluginId;
		if (id === loadedDetailId) return;
		loadedDetailId = id;
		untrack(() => {
			if (id) void loadDetail(id);
			else {
				selectedPlugin = null;
				detailTab = 'overview';
			}
		});
	});

	$effect(() => {
		if (liveSelected && !isPluginDetailTab(liveSelected, adminTabHooks, detailTab, $authStore.user?.account_type)) {
			detailTab = 'overview';
		}
	});
</script>

<div class="flex h-full flex-col">
	{#if $loading}
		<div class="flex items-center justify-center py-20">
			<div class="text-center">
				<Spinner size="lg" />
				<p class="text-fg-muted mt-4">Loading plugins...</p>
			</div>
		</div>
	{:else if $error}
		<LoadErrorState title="Error loading plugins" message={$error} onRetry={() => pluginStore.loadPlugins()} retrying={$loading} />
	{:else if $plugins.length === 0}
		<EmptyState
			icon="extension"
			title="No plugins found yet"
			description={`Place plugins in content/plugins/local/ or content/plugins/marketplace/, then click "Scan for Plugins".`}
		>
			{#snippet actions()}
				<Button variant="primary" icon={scanning ? undefined : 'search'} loading={scanning} disabled={scanning} onclick={scanForPlugins}>
					{scanning ? 'Scanning...' : 'Scan for Plugins'}
				</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<LibraryShell
			title="Plugins"
			persistKey="admin-plugins-library"
			sections={PLUGIN_SECTIONS}
			{section}
			onSelectSection={selectSection}
			{sectionCounts}
			count={visiblePlugins.length}
			{detailOpen}
			heightClass="h-full"
			filterChips={chips}
			onRemoveChip={(key) => updateFilters(clearPluginFilterChip(filters, key))}
			onClearFilters={() => updateFilters(clearAllPluginFilters(filters))}
			loadedCount={visiblePlugins.length}
			total={$plugins.length}
		>
			{#snippet toolbar()}
				<LibraryFilterBar
					q={filters.q}
					onQueryChange={(value) => updateFilters({ ...filters, q: value })}
					searchPlaceholder="Search plugins by name, id, tag…"
					sortBy={filters.sortBy}
					sortOptions={PLUGIN_SORT_OPTIONS}
					onSortChange={(value) => updateFilters({ ...filters, sortBy: value as PluginFilters['sortBy'] })}
					{filterCount}
				>
					{#snippet popover(close)}
						<PluginFiltersPopover {filters} onChange={updateFilters} onClose={close} />
					{/snippet}
				</LibraryFilterBar>
			{/snippet}

			{#snippet primary()}
				<LibraryDensityToggle />
				<Button variant="secondary" size="sm" icon={scanning ? undefined : 'search'} loading={scanning || $loading} disabled={scanning || $loading} onclick={scanForPlugins}>
					{scanning ? 'Scanning...' : 'Scan for plugins'}
				</Button>
			{/snippet}

			{#if detailOpen}
				<div class="h-full min-h-0 flex flex-col">
					{#if detailLoading}
						<div class="h-full flex items-center justify-center">
							<Spinner size="lg" />
						</div>
					{:else if liveSelected}
						<DetailHeader title={liveSelected.name} icon={pluginIcon} backLabel="Plugins" onBack={backToList} chipItems={pluginChips}>
							{#snippet enabledSwitch()}
								<Switch
									checked={liveSelected.enabled}
									busy={$pendingPluginIds.has(liveSelected.id)}
									disabled={liveSelected.state === 'error'}
									size="lg"
									onchange={() => togglePlugin(liveSelected)}
									label={liveSelected.state === 'error' ? 'Plugin has an invalid manifest and cannot be enabled' : liveSelected.enabled ? 'Disable plugin' : 'Enable plugin'}
								/>
							{/snippet}
						</DetailHeader>

						{#if showHiddenAdminTabsHint}
							<div class="px-4 sm:px-5 pt-3">
								<Alert variant="info" icon density="compact">Enable this plugin to see its additional tabs.</Alert>
							</div>
						{/if}

						<DetailTabs tabs={detailTabs} active={detailTab} onSelect={(id) => (detailTab = id)} ariaLabel="Plugin details" />

						{#if detailTab === 'overview'}
							{#snippet overviewMain()}
								{#if liveSelected.state === 'error' && liveSelected.error}
									<Alert variant="danger" icon title="Invalid manifest">{liveSelected.error}</Alert>
								{/if}

								<DetailSection label="Overview">
									<div class="space-y-4">
										<p class="text-sm text-fg-muted">
											{liveSelected.description || 'No description available'}
										</p>

										{#if liveSelected.tags && liveSelected.tags.length > 0}
											<div class="flex flex-wrap gap-1.5">
												{#each liveSelected.tags as tag}
													<Badge variant="neutral" size="sm">{tag}</Badge>
												{/each}
											</div>
										{/if}

										{#if liveSelected.capabilities && liveSelected.capabilities.length > 0}
											<div class="flex flex-wrap gap-1.5">
												{#each liveSelected.capabilities as cap}
													<Badge variant="info" size="sm">{cap}</Badge>
												{/each}
											</div>
										{/if}

										<KVGrid>
											{#if liveSelected.author}
												<KVItem label="Author">{liveSelected.author}</KVItem>
											{/if}
											{#if liveSelected.installed_at}
												<KVItem label="Installed" mono>{parseServerDate(liveSelected.installed_at)?.toLocaleDateString() ?? 'Unknown'}</KVItem>
											{/if}
											{#if liveSelected.homepage}
												<KVItem label="Homepage">
													<a href={liveSelected.homepage} target="_blank" rel="noreferrer" class="text-signal hover:underline truncate block">
													{liveSelected.homepage}
												</a>
												</KVItem>
											{/if}
											{#if liveSelected.repository}
												<KVItem label="Repository">
													<a href={liveSelected.repository} target="_blank" rel="noreferrer" class="text-signal hover:underline truncate block">
													{liveSelected.repository}
												</a>
												</KVItem>
											{/if}
										</KVGrid>
									</div>
								</DetailSection>
							{/snippet}

							{#if liveSelected.hooks && liveSelected.hooks.length > 0}
								{#snippet hooksAside()}
									<DetailSection label="Registered Hooks">
										<div class="space-y-2">
											{#each liveSelected.hooks as hook}
												<div class="p-3 {DETAIL_INSET_CLASS}">
													<div class="flex items-center justify-between mb-1 gap-2">
														<span class="font-medium text-sm text-fg truncate">{hook.hook_name}</span>
														<Badge variant={hook.hook_type === 'frontend' ? 'signal' : 'neutral'} size="sm" class="uppercase flex-shrink-0">
															{hook.hook_type}
														</Badge>
													</div>
													{#if hook.handler_path}
														<p class="text-xs text-fg-subtle font-mono mt-1 break-all">{hook.handler_path}</p>
													{/if}
													{#if hook.component_path}
														<p class="text-xs text-fg-subtle font-mono mt-1 break-all">{hook.component_path}</p>
													{/if}
													{#if hook.position}
														<p class="text-xs text-fg-subtle mt-1">Position: {hook.position}</p>
													{/if}
												</div>
											{/each}
										</div>
									</DetailSection>
								{/snippet}
								<DetailBody>
									<DetailLayout main={overviewMain} aside={hooksAside} />
								</DetailBody>
							{:else}
								<DetailBody>
									<DetailLayout main={overviewMain} />
								</DetailBody>
							{/if}
						{:else if detailTab === 'settings'}
							<DetailBody>
								<DetailLayout>
									{#snippet main()}
								<DetailSection label="Settings">
									{#if liveSelected.settings_schema && liveSelected.settings_schema.length > 0}
										<form
											id="plugin-settings-form"
											onsubmit={(event) => {
												event.preventDefault();
												saveSettings();
											}}
											class="space-y-4"
										>
											{#each liveSelected.settings_schema as schema}
												{#if schema.type === 'info'}
													<div class="p-4 {DETAIL_INSET_CLASS}">
														<div class="flex items-start gap-3">
															<Icon name="info" className="mt-0.5 h-4 w-4 flex-shrink-0 text-fg-subtle" />
															<div class="min-w-0 flex-1">
																<p class="text-sm font-medium text-fg">{schema.label}</p>
																{#if schema.description}
																	<p class="mt-1 text-sm leading-relaxed text-fg-muted">{schema.description}</p>
																{/if}
																{#if schema.href}
																	<div class="mt-3">
																		<Button variant="secondary" size="sm" href={schema.href} icon="arrow-right">
																			{schema.link_label || 'Open'}
																		</Button>
																	</div>
																{/if}
															</div>
														</div>
													</div>
												{:else}
												<div>
													<label for={schema.name} class="block text-sm font-medium text-fg-muted mb-1">
														{schema.label}
														{#if schema.required}
															<span class="text-danger">*</span>
														{/if}
													</label>
													{#if schema.description}
														<p class="text-xs text-fg-subtle mb-1">{schema.description}</p>
													{/if}
													{#if schema.type === 'boolean'}
														<Switch
															checked={!!settingsValues[schema.name]}
															onchange={(v) => (settingsValues[schema.name] = v)}
															label={schema.label}
															id={schema.name}
														/>
													{:else}
														<Input
															id={schema.name}
															type={getInputType(schema)}
															bind:value={settingsValues[schema.name]}
															placeholder={schema.default !== undefined ? `Default: ${schema.default}` : 'Enter value...'}
														/>
													{/if}
												</div>
												{/if}
											{/each}
										</form>
									{:else}
										<EmptyState icon="settings" title="No configurable settings" compact />
									{/if}
								</DetailSection>
								{/snippet}
							</DetailLayout>
							</DetailBody>
						{:else if activeContributedTab && activeTabComponentPromise}
							<DetailBody>
								<DetailLayout>
									{#snippet main()}
								{#await activeTabComponentPromise}
									<div class="flex items-center justify-center py-10">
										<Spinner size="lg" />
									</div>
								{:then Component}
									{#if Component}
										<Component pluginId={liveSelected.id} plugin={liveSelected} />
									{:else}
										<Alert variant="danger" icon title="Failed to load tab">
											Could not load the "{activeContributedTab.label}" component ({activeContributedTab.componentPath}).
										</Alert>
									{/if}
								{:catch err}
									<Alert variant="danger" icon title="Failed to load tab">{err?.message || 'Unknown error'}</Alert>
								{/await}
									{/snippet}
								</DetailLayout>
							</DetailBody>
						{/if}

						{#if detailTab === 'settings' && liveSelected.settings_schema && liveSelected.settings_schema.length > 0}
							<DetailFooter
								dirtyCount={settingsDirtyKeys.length}
								{saving}
								onSave={saveSettings}
								onDiscard={resetSettings}
							/>
						{/if}
					{:else}
						<div class="flex h-full items-center justify-center">
							<EmptyState icon="document" title="Plugin not found" description="This plugin may have been removed.">
								{#snippet actions()}
									<Button variant="ghost" size="sm" onclick={backToList}>Back to plugins</Button>
								{/snippet}
							</EmptyState>
						</div>
					{/if}
				</div>
			{:else}
				<div class="flex h-full flex-col overflow-hidden">
					{#if scanResult}
						<div class="flex-shrink-0 px-4 pt-4 sm:px-6">
							<Alert variant="success" icon title="Scan Complete">
								{#if scanResult.newPlugins > 0 || scanResult.updatedPlugins > 0}
									Found {scanResult.newPlugins} new plugin{scanResult.newPlugins !== 1 ? 's' : ''}{scanResult.updatedPlugins > 0 ? ` and ${scanResult.updatedPlugins} updated plugin${scanResult.updatedPlugins !== 1 ? 's' : ''}` : ''}.
								{:else}
									No new plugins found.
								{/if}
							</Alert>
						</div>
					{/if}
					<div class="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
						{#if visiblePlugins.length === 0}
							<div class="flex h-full items-center justify-center">
								<EmptyState icon="search" title="No plugins match" description="No plugins match the current search and filters." compact>
									{#snippet actions()}
										<Button variant="ghost" size="sm" onclick={() => updateFilters(clearAllPluginFilters(filters))}>Clear filters</Button>
									{/snippet}
								</EmptyState>
							</div>
						{:else}
							<div
								class="grid gap-3 {$libraryCardDensity === 'compact'
									? 'grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2'
									: 'grid-cols-[repeat(auto-fill,minmax(300px,1fr))]'}"
								role="list"
								aria-label="Plugin catalog"
							>
								{#each visiblePlugins as plugin (plugin.id)}
									<PluginCard
										{plugin}
										busy={$pendingPluginIds.has(plugin.id)}
										dense={$libraryCardDensity === 'compact'}
										onOpen={openPlugin}
										onToggle={togglePlugin}
									/>
								{/each}
							</div>
						{/if}
					</div>
				</div>
			{/if}
		</LibraryShell>
	{/if}
</div>
