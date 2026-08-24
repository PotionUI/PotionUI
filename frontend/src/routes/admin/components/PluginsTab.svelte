<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { pluginStore, plugins, loading, error, pendingPluginIds, type Plugin, type PluginSetting, type PluginSettingSchema } from '$lib/stores/plugins';
	import { Button, Badge, Spinner, Input, Kbd, EmptyState, Switch, Alert } from '$lib/components/ui';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { pluginCategories, resolveCategory, type PluginCategoryId } from '$lib/plugins/categories';

	let selectedPlugin: Plugin | null = null;
	let settingsSchema: PluginSettingSchema[] = [];
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

	// Load plugins on mount
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

	// Scan for new plugins
	async function scanForPlugins() {
		scanning = true;
		scanResult = null;
		const result = await pluginStore.scanPlugins();
		scanning = false;
		if (result) {
			scanResult = result;
			// Clear the scan result message after 5 seconds
			setTimeout(() => {
				scanResult = null;
			}, 5000);
		}
	}

	// Toggle plugin enabled state
	function togglePlugin(plugin: Plugin) {
		pluginStore.togglePlugin(plugin.id, !plugin.enabled);
	}

	// Select plugin for details view
	async function selectPlugin(plugin: Plugin) {
		// Fetch full plugin details including settings_schema and settings_values
		const pluginDetails = await pluginStore.getPluginDetails(plugin.id);
		if (pluginDetails) {
			selectedPlugin = pluginDetails;
			settingsSchema = pluginDetails.settings_schema || [];
			settingsValues = { ...(pluginDetails.settings_values || {}) };

			// Initialize missing values with defaults from schema
			for (const schema of settingsSchema) {
				if (!(schema.name in settingsValues) && schema.default !== undefined) {
					settingsValues[schema.name] = schema.default;
				}
			}
		}
	}

	function handleRowKeydown(e: KeyboardEvent, plugin: Plugin) {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			selectPlugin(plugin);
		}
	}

	// Save settings
	async function saveSettings() {
		if (!selectedPlugin) return;
		saving = true;
		const success = await pluginStore.updatePluginSettings(selectedPlugin.id, settingsValues);
		saving = false;
		if (success) {
			// Reload plugin details to reflect saved state
			const pluginDetails = await pluginStore.getPluginDetails(selectedPlugin.id);
			if (pluginDetails) {
				selectedPlugin = pluginDetails;
				settingsValues = { ...(pluginDetails.settings_values || {}) };
			}
		}
	}

	// Close details
	function closeDetails() {
		selectedPlugin = null;
		settingsSchema = [];
		settingsValues = {};
	}

	// Get input type for setting
	function getInputType(schema: PluginSettingSchema): string {
		if (schema.format === 'password') return 'password';
		if (schema.type === 'number') return 'number';
		return 'text';
	}

	function clearFilters() {
		searchQuery = '';
		stateFilter = 'all';
	}

	// Filtered plugin list — search + state filter
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

	// Group filtered plugins by category, preserving the canonical category order.
	// Sections with no matches are omitted entirely.
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
</script>

<div class="space-y-4">
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

	{#if !$loading && !$error && $plugins.length > 0}
		<AdminFilterBar
			search={pluginSearch}
			filters={pluginStateFilters}
			activeCount={activeFilterCount}
			onClear={clearFilters}
		/>
	{/if}

	<!-- Scan Result Notification -->
	{#if scanResult}
		<Alert variant="success" icon title="Scan Complete">
			{#if scanResult.newPlugins > 0 || scanResult.updatedPlugins > 0}
				Found {scanResult.newPlugins} new plugin{scanResult.newPlugins !== 1 ? 's' : ''}{scanResult.updatedPlugins > 0 ? ` and ${scanResult.updatedPlugins} updated plugin${scanResult.updatedPlugins !== 1 ? 's' : ''}` : ''}.
			{:else}
				No new plugins found.
			{/if}
		</Alert>
	{/if}

	<!-- Loading State -->
	{#if $loading}
		<div class="flex items-center justify-center py-20">
			<div class="text-center">
				<Spinner size="lg" />
				<p class="text-fg-muted mt-4">Loading plugins...</p>
			</div>
		</div>

		<!-- Error State -->
	{:else if $error}
		<Alert variant="danger" icon title="Error loading plugins">{$error}</Alert>

		<!-- Empty State (no plugins installed at all) -->
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

		<!-- Empty State (filters produced no matches) -->
	{:else if groupedPlugins.length === 0}
		<EmptyState
			icon="search"
			title="No plugins match"
			description="No plugins match the current search and filters."
			compact
		>
			{#snippet actions()}
				<Button variant="secondary" size="sm" onclick={clearFilters}>Clear filters</Button>
			{/snippet}
		</EmptyState>

		<!-- Categorised Plugin List -->
	{:else}
		<div class="space-y-6">
			{#each groupedPlugins as group (group.category.id)}
				<section>
					<div class="flex items-center gap-2 mb-2">
						<Icon name={group.category.icon} className="w-3.5 h-3.5 text-fg-subtle" />
						<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
							{group.category.label}
						</h3>
						<span class="font-mono text-2xs tabular-nums text-fg-subtle ml-1">
							{group.items.length}
						</span>
						<div class="flex-1 h-px bg-line ml-2"></div>
					</div>

					<div class="rounded-lg border border-line bg-surface-2 overflow-hidden divide-y divide-line">
						{#each group.items as plugin (plugin.id)}
							<div
								class="group flex items-start gap-3 px-4 py-2.5 hover:bg-surface-3/50 transition-colors cursor-pointer focus:outline-none focus:bg-surface-3/50"
								role="button"
								tabindex="0"
								on:click={() => selectPlugin(plugin)}
								on:keydown={(e) => handleRowKeydown(e, plugin)}
							>
								<span class="mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0 {stateDotClass(plugin)}"></span>

								<div class="min-w-0 flex-1">
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
								</div>

								<div class="flex items-center gap-3 flex-shrink-0 pl-2">
									<Switch
										checked={plugin.enabled}
										busy={$pendingPluginIds.has(plugin.id)}
										disabled={plugin.state === 'error'}
										onchange={() => togglePlugin(plugin)}
										onclick={(e) => e.stopPropagation()}
										label={plugin.state === 'error' ? 'Plugin has an invalid manifest and cannot be enabled' : plugin.enabled ? 'Disable plugin' : 'Enable plugin'}
									/>
									<Icon name="chevron-right" className="w-3.5 h-3.5 text-fg-subtle opacity-0 group-hover:opacity-100 transition-opacity" />
								</div>
							</div>
						{/each}
					</div>
				</section>
			{/each}
		</div>
	{/if}
</div>

<!-- Plugin Details Modal -->
{#if selectedPlugin}
	<BaseModal isOpen={true} title={selectedPlugin.name} size="lg" on:close={closeDetails}>
		<div class="px-6 py-4 space-y-6">
			<!-- Version / type / state -->
			<div class="flex items-center gap-2 flex-wrap -mt-2">
				<span class="text-sm font-mono tabular-nums text-fg-subtle">v{selectedPlugin.version}</span>
				<Badge variant="neutral" class="font-mono uppercase">{selectedPlugin.type}</Badge>
				{#if selectedPlugin.source}
					<Badge variant="neutral" class="font-mono uppercase">{selectedPlugin.source}</Badge>
				{/if}
				{#if selectedPlugin.enabled}
					<Badge variant="success" class="uppercase">Enabled</Badge>
				{:else}
					<Badge variant="neutral" class="uppercase">Disabled</Badge>
				{/if}
			</div>

			<!-- Description -->
				<div>
					<h3 class="text-sm font-semibold text-fg-muted mb-2">Description</h3>
					<p class="text-sm text-fg-muted">
						{selectedPlugin.description || 'No description available'}
					</p>
				</div>

				<!-- Tags -->
				{#if selectedPlugin.tags && selectedPlugin.tags.length > 0}
					<div class="flex flex-wrap gap-1.5">
						{#each selectedPlugin.tags as tag}
							<Badge variant="neutral" size="sm">{tag}</Badge>
						{/each}
					</div>
				{/if}

				<!-- Meta Information -->
				<div class="grid grid-cols-2 gap-4 text-sm">
					{#if selectedPlugin.author}
						<div>
							<span class="text-fg-subtle">Author</span>
							<p class="font-medium text-fg">{selectedPlugin.author}</p>
						</div>
					{/if}
					{#if selectedPlugin.installed_at}
						<div>
							<span class="text-fg-subtle">Installed</span>
							<p class="font-medium text-fg font-mono tabular-nums">
								{new Date(selectedPlugin.installed_at).toLocaleDateString()}
							</p>
						</div>
					{/if}
					{#if selectedPlugin.homepage}
						<div>
							<span class="text-fg-subtle">Homepage</span>
							<p class="font-medium text-fg truncate">
								<a href={selectedPlugin.homepage} target="_blank" rel="noreferrer" class="text-signal hover:underline">
									{selectedPlugin.homepage}
								</a>
							</p>
						</div>
					{/if}
					{#if selectedPlugin.repository}
						<div>
							<span class="text-fg-subtle">Repository</span>
							<p class="font-medium text-fg truncate">
								<a href={selectedPlugin.repository} target="_blank" rel="noreferrer" class="text-signal hover:underline">
									{selectedPlugin.repository}
								</a>
							</p>
						</div>
					{/if}
				</div>

				<!-- Hooks Information -->
				{#if selectedPlugin.hooks && selectedPlugin.hooks.length > 0}
					<div>
						<h3 class="text-sm font-semibold text-fg-muted mb-3">Registered Hooks</h3>
						<div class="space-y-2">
							{#each selectedPlugin.hooks as hook}
								<div class="bg-surface-2 rounded-lg p-3 border border-line">
									<div class="flex items-center justify-between mb-1">
										<span class="font-medium text-sm text-fg">{hook.hook_name}</span>
										<Badge variant={hook.hook_type === 'frontend' ? 'signal' : 'neutral'} size="sm" class="uppercase">
											{hook.hook_type}
										</Badge>
									</div>
									{#if hook.handler_path}
										<p class="text-xs text-fg-subtle font-mono mt-1">{hook.handler_path}</p>
									{/if}
									{#if hook.component_path}
										<p class="text-xs text-fg-subtle font-mono mt-1">{hook.component_path}</p>
									{/if}
									{#if hook.position}
										<p class="text-xs text-fg-subtle mt-1">Position: {hook.position}</p>
									{/if}
								</div>
							{/each}
						</div>
					</div>
				{/if}

				<!-- Settings Form -->
				{#if settingsSchema.length > 0}
					<div>
						<h3 class="text-sm font-semibold text-fg-muted mb-3">Settings</h3>
						<form on:submit|preventDefault={saveSettings} class="space-y-4">
							{#each settingsSchema as schema}
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
										<input
											id={schema.name}
											type="checkbox"
											bind:checked={settingsValues[schema.name]}
											class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
										/>
									{:else}
										<input
											id={schema.name}
											type={getInputType(schema)}
											bind:value={settingsValues[schema.name]}
											class="input"
											placeholder={schema.default !== undefined ? `Default: ${schema.default}` : 'Enter value...'}
										/>
									{/if}
								</div>
							{/each}

							<div class="flex gap-3 pt-2">
								<Button type="submit" variant="primary" class="flex-1" loading={saving}>
									{saving ? 'Saving...' : 'Save Settings'}
								</Button>
								<Button
									type="button"
									variant="secondary"
									onclick={() => {
										// Reset to saved values or defaults
										settingsValues = { ...(selectedPlugin?.settings_values || {}) };
										for (const schema of settingsSchema) {
											if (!(schema.name in settingsValues) && schema.default !== undefined) {
												settingsValues[schema.name] = schema.default;
											}
										}
									}}
								>
									Reset
								</Button>
							</div>
						</form>
					</div>
				{:else}
					<div class="bg-surface-2 rounded-lg p-4 text-center text-sm text-fg-subtle">
						This plugin has no configurable settings
					</div>
				{/if}
		</div>
	</BaseModal>
{/if}
