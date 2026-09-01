<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { pluginStore, plugins, loading, error, pendingPluginIds, type Plugin, type PluginSettingSchema } from '$lib/stores/plugins';
	import { Button, Badge, Spinner, Input, Kbd, EmptyState, Switch, Alert } from '$lib/components/ui';
	import { MasterDetailLayout, DetailEmptyState } from '$lib/components/master-detail';
	import { Pane, PaneRow, PaneGroupHeader } from '$lib/components/pane';
	import { DetailHeader, DetailBody, DetailSection, DetailFooter, KVGrid, KVItem } from '$lib/components/detail';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { pluginCategories, resolveCategory } from '$lib/plugins/categories';

	let selectedPluginId: string | null = null;
	// Raw fetch from GET /api/plugins/{id} - the only source for hooks,
	// settings_schema, tags, author, and the other fields the list endpoint
	// doesn't return. `enabled`/`state`/`error` are read from the live
	// `$plugins` store instead (see `liveSelected`) so a toggle reflects here
	// without a refetch.
	let selectedPlugin: Plugin | null = null;
	let detailLoading = false;
	let settingsValues: Record<string, any> = {};
	let saving = false;
	let scanning = false;
	let scanResult: { newPlugins: number; updatedPlugins: number } | null = null;

	let searchQuery = '';
	const SEARCH_INPUT_ID = 'plugins-catalogue-search';
	type StateFilter = 'all' | 'enabled' | 'disabled' | 'error';
	let stateFilter: StateFilter = 'all';

	const stateFilters: { id: StateFilter; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'enabled', label: 'Enabled' },
		{ id: 'disabled', label: 'Disabled' },
		{ id: 'error', label: 'Errored' }
	];

	onMount(async () => {
		await pluginStore.loadPlugins();
		window.addEventListener('keydown', handleGlobalKeydown);
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('keydown', handleGlobalKeydown);
		}
	});

	function handleGlobalKeydown(e: KeyboardEvent) {
		if (e.key !== '/') return;
		const target = e.target as HTMLElement | null;
		const tag = target?.tagName;
		if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return;
		e.preventDefault();
		document.getElementById(SEARCH_INPUT_ID)?.focus();
	}

	async function scanForPlugins() {
		scanning = true;
		scanResult = null;
		const result = await pluginStore.scanPlugins();
		scanning = false;
		if (result) {
			scanResult = result;
			setTimeout(() => {
				scanResult = null;
			}, 5000);
		}
	}

	function togglePlugin(plugin: Plugin) {
		pluginStore.togglePlugin(plugin.id, !plugin.enabled);
	}

	// Select a plugin for the detail pane - fetches full details (settings
	// schema/values, hooks) the list endpoint doesn't include.
	async function selectPlugin(pluginId: string) {
		selectedPluginId = pluginId;
		detailLoading = true;
		const pluginDetails = await pluginStore.getPluginDetails(pluginId);
		detailLoading = false;
		if (!pluginDetails) return;
		selectedPlugin = pluginDetails;
		initSettingsValues(pluginDetails);
	}

	function initSettingsValues(detail: Plugin) {
		const schema = detail.settings_schema || [];
		const values: Record<string, any> = { ...(detail.settings_values || {}) };
		for (const s of schema) {
			if (!(s.name in values) && s.default !== undefined) values[s.name] = s.default;
		}
		settingsValues = values;
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
				settingsValues = { ...(pluginDetails.settings_values || {}) };
			}
		}
	}

	function resetSettings() {
		if (!selectedPlugin) return;
		initSettingsValues(selectedPlugin);
	}

	function getInputType(schema: PluginSettingSchema): string {
		if (schema.format === 'password') return 'password';
		if (schema.type === 'number') return 'number';
		return 'text';
	}

	function clearFilters() {
		searchQuery = '';
		stateFilter = 'all';
	}

	$: filteredPlugins = $plugins.filter((plugin) => {
		if (stateFilter === 'enabled' && !(plugin.enabled && plugin.state !== 'error')) return false;
		if (stateFilter === 'disabled' && (plugin.enabled || plugin.state === 'error')) return false;
		if (stateFilter === 'error' && plugin.state !== 'error') return false;

		if (searchQuery.trim()) {
			const q = searchQuery.trim().toLowerCase();
			const haystack = [
				plugin.name,
				plugin.id,
				plugin.description ?? '',
				...(plugin.tags ?? []),
				...(plugin.capabilities ?? [])
			]
				.join(' ')
				.toLowerCase();
			if (!haystack.includes(q)) return false;
		}

		return true;
	});

	// Grouped by category, preserving the canonical category order. Sections
	// with no matches are omitted entirely.
	$: groupedPlugins = pluginCategories
		.map((cat) => ({
			category: cat,
			items: filteredPlugins.filter((p) => resolveCategory(p.category).id === cat.id)
		}))
		.filter((group) => group.items.length > 0);

	function stateDotClass(plugin: Plugin): string {
		if (plugin.state === 'error') return 'bg-danger';
		if (plugin.enabled) return 'bg-success';
		return 'bg-fg-subtle';
	}

	const MAX_CAPABILITY_BADGES = 3;
	$: enabledPluginsCount = $plugins.filter((p) => p.enabled).length;
	$: activeFilterCount = Number(!!searchQuery.trim()) + Number(stateFilter !== 'all');

	// `selectedPlugin` is a point-in-time fetch; `enabled`/`state`/`error` come
	// from the live store instead so toggling in the detail header (or a stale
	// row elsewhere) is reflected without refetching the whole detail payload.
	$: listMatch = selectedPluginId ? $plugins.find((p) => p.id === selectedPluginId) : undefined;
	$: liveSelected = selectedPlugin
		? {
				...selectedPlugin,
				enabled: listMatch?.enabled ?? selectedPlugin.enabled,
				state: listMatch?.state ?? selectedPlugin.state,
				error: listMatch?.error ?? selectedPlugin.error
			}
		: null;
</script>

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="Plugin Management"
		icon="extension"
		counts={[
			{ label: 'plugins', value: $plugins.length },
			{ label: 'enabled', value: enabledPluginsCount, tone: 'success' }
		]}
	>
		{#snippet actions()}
			<Button variant="secondary" size="sm" icon={scanning ? undefined : 'search'} loading={scanning || $loading} disabled={scanning || $loading} onclick={scanForPlugins}>
				{scanning ? 'Scanning...' : 'Scan for Plugins'}
			</Button>
		{/snippet}
	</AdminTabShell>

	{#if scanResult}
		<Alert variant="success" icon title="Scan Complete">
			{#if scanResult.newPlugins > 0 || scanResult.updatedPlugins > 0}
				Found {scanResult.newPlugins} new plugin{scanResult.newPlugins !== 1 ? 's' : ''}{scanResult.updatedPlugins > 0 ? ` and ${scanResult.updatedPlugins} updated plugin${scanResult.updatedPlugins !== 1 ? 's' : ''}` : ''}.
			{:else}
				No new plugins found.
			{/if}
		</Alert>
	{/if}

	{#if $loading}
		<div class="flex items-center justify-center py-20">
			<div class="text-center">
				<Spinner size="lg" />
				<p class="text-fg-muted mt-4">Loading plugins...</p>
			</div>
		</div>
	{:else if $error}
		<Alert variant="danger" icon title="Error loading plugins">{$error}</Alert>
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
		{#snippet pluginSearch()}
			<div class="relative">
				<Icon name="search" className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
				<Input
					id={SEARCH_INPUT_ID}
					bind:value={searchQuery}
					placeholder="Search plugins..."
					class="pl-8 pr-8 text-sm h-8"
				/>
				{#if !searchQuery}
					<span class="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none">
						<Kbd keys="/" />
					</span>
				{/if}
			</div>
		{/snippet}
		{#snippet pluginStateFilters()}
			<div class="flex items-center gap-1 bg-surface-2 border border-line rounded p-0.5">
				{#each stateFilters as f}
					<button
						type="button"
						class="px-2.5 py-1 text-xs font-medium rounded transition-colors {stateFilter === f.id
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:text-fg hover:bg-surface-3'}"
						on:click={() => (stateFilter = f.id)}
					>
						{f.label}
					</button>
				{/each}
			</div>
		{/snippet}

		<AdminFilterBar
			search={pluginSearch}
			filters={pluginStateFilters}
			activeCount={activeFilterCount}
			onClear={clearFilters}
		/>

		<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
			<MasterDetailLayout leftWidth={340} minWidth={280} maxWidth={480} storageKey="admin-plugins-width">
				<div slot="list" class="h-full min-h-0">
					<Pane
						label="Plugins"
						count={filteredPlugins.length}
						isEmpty={filteredPlugins.length === 0}
						bodyRole="listbox"
						ariaLabel="Plugins"
					>
						{#snippet empty()}
							<div class="p-4 h-full flex items-center justify-center">
								<EmptyState title="No plugins match" description="No plugins match the current search and filters." icon="search" compact>
									{#snippet actions()}<Button variant="ghost" size="sm" onclick={clearFilters}>Clear filters</Button>{/snippet}
								</EmptyState>
							</div>
						{/snippet}

						{#snippet children()}
							{#each groupedPlugins as group (group.category.id)}
								<PaneGroupHeader icon={group.category.icon} label={group.category.label} count={group.items.length} />
								{#each group.items as plugin (plugin.id)}
									{#snippet pluginLeading()}
										<span class="mt-0.5 w-1.5 h-1.5 rounded-full flex-shrink-0 {stateDotClass(plugin)}"></span>
									{/snippet}
									{#snippet pluginBody()}
										<div class="flex items-baseline gap-2">
											<span class="text-[13px] font-medium text-fg truncate">{plugin.name}</span>
											<span class="text-xs font-mono tabular-nums text-fg-subtle flex-shrink-0">v{plugin.version}</span>
										</div>
										<p class="text-xs text-fg-muted truncate mt-0.5">
											{plugin.description || 'No description available'}
										</p>
										{#if plugin.state === 'error' && plugin.error}
											<p class="text-xs text-danger mt-1 break-words">{plugin.error}</p>
										{/if}
										<div class="flex flex-wrap items-center gap-1.5 mt-1.5">
											{#if plugin.source}
												<Badge variant="neutral" size="sm" class="font-mono uppercase">{plugin.source}</Badge>
											{/if}
											<Badge variant="neutral" size="sm" class="font-mono uppercase">{plugin.type}</Badge>
											{#each (plugin.capabilities ?? []).slice(0, MAX_CAPABILITY_BADGES) as cap}
												<Badge variant="info" size="sm">{cap}</Badge>
											{/each}
											{#if (plugin.capabilities ?? []).length > MAX_CAPABILITY_BADGES}
												<span class="text-2xs font-mono tabular-nums text-fg-subtle">
													+{(plugin.capabilities ?? []).length - MAX_CAPABILITY_BADGES}
												</span>
											{/if}
											{#if (plugin.hook_count ?? 0) > 0}
												<Badge variant="neutral" size="sm" class="font-mono tabular-nums">{plugin.hook_count} HOOKS</Badge>
											{/if}
											{#if (plugin.settings_count ?? 0) > 0}
												<Badge variant="neutral" size="sm" class="font-mono tabular-nums">{plugin.settings_count} SETTINGS</Badge>
											{/if}
										</div>
									{/snippet}
									<PaneRow
										selected={selectedPluginId === plugin.id}
										onclick={() => selectPlugin(plugin.id)}
										leading={pluginLeading}
										children={pluginBody}
									/>
								{/each}
							{/each}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					{#if detailLoading}
						<div class="h-full flex items-center justify-center">
							<Spinner size="lg" />
						</div>
					{:else if liveSelected}
						<DetailHeader title={liveSelected.name}>
							{#snippet chips()}
								<Badge variant="neutral" size="sm" class="font-mono tabular-nums">v{liveSelected.version}</Badge>
								<Badge variant="neutral" size="sm" class="font-mono uppercase">{liveSelected.type}</Badge>
								{#if liveSelected.source}
									<Badge variant="neutral" size="sm" class="font-mono uppercase">{liveSelected.source}</Badge>
								{/if}
								{#if liveSelected.state === 'error'}
									<Badge variant="danger" size="sm" dot class="uppercase">Error</Badge>
								{/if}
							{/snippet}
							{#snippet actions()}
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

							<DetailBody>
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

										<KVGrid>
											{#if liveSelected.author}
												<KVItem label="Author">{liveSelected.author}</KVItem>
											{/if}
											{#if liveSelected.installed_at}
												<KVItem label="Installed" mono>{new Date(liveSelected.installed_at).toLocaleDateString()}</KVItem>
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

								{#if liveSelected.hooks && liveSelected.hooks.length > 0}
									<DetailSection label="Registered Hooks">
										<div class="space-y-2">
											{#each liveSelected.hooks as hook}
												<div class="bg-surface-2 rounded-lg p-3 border border-line">
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
								{/if}

								<DetailSection label="Settings">
									{#if liveSelected.settings_schema && liveSelected.settings_schema.length > 0}
										<form id="plugin-settings-form" on:submit|preventDefault={saveSettings} class="space-y-4">
											{#each liveSelected.settings_schema as schema}
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
											{/each}
										</form>
									{:else}
										<div class="bg-surface-2 rounded-lg p-4 text-center text-sm text-fg-subtle">
											This plugin has no configurable settings
										</div>
									{/if}
								</DetailSection>
							</DetailBody>

						{#if liveSelected.settings_schema && liveSelected.settings_schema.length > 0}
							<DetailFooter>
								<Button type="button" variant="ghost" size="sm" onclick={resetSettings}>Reset</Button>
								<Button type="button" variant="primary" size="sm" loading={saving} onclick={saveSettings}>
									{saving ? 'Saving...' : 'Save Settings'}
								</Button>
							</DetailFooter>
						{/if}
					{:else}
						<DetailEmptyState message="Select a plugin to view its details" icon="document" />
					{/if}
				</div>
			</MasterDetailLayout>
		</section>
	{/if}
</div>
