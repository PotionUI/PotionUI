<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth';
	import { PageHeader } from '$lib/components/ui';
	import LazyAdminTab from './components/LazyAdminTab.svelte';
	import { contributionsForSlot } from '$lib/extensions/extensionSlots';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';

	// Plugin-contributed admin tabs (A5 `admin.tabs` extension slot).
	$: adminTabContributions = contributionsForSlot('admin.tabs');
	$: pluginTabs = $adminTabContributions.map((c) => ({
		id: `plugin:${c.plugin_id}:${c.component}`,
		label: c.label || c.component,
		icon: 'puzzle',
		contribution: c
	}));

	let activeTab: string = 'settings';
	let initialDocId: string | null = null;
	let loading = true;

	$: currentUser = $authStore.user;
	$: isAuthenticated = $authStore.isAuthenticated;
	$: authLoading = $authStore.loading;
	// The 'sessions' tab was merged into 'llm' (LLM / Assistant) with an
	// internal view switcher; rewrite old muscle-memory links (?tab=sessions)
	// to ?tab=llm&view=sessions so they land on the same sub-view.
	$: if ($page.url.searchParams.get('tab') === 'sessions') {
		const url = new URL($page.url);
		url.searchParams.set('tab', 'llm');
		url.searchParams.set('view', 'sessions');
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// 'groups' (User Groups) was merged into 'users' — rewrite old links the
	// same way.
	$: if ($page.url.searchParams.get('tab') === 'groups') {
		const url = new URL($page.url);
		url.searchParams.set('tab', 'users');
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// 'attributes' was merged into 'models' with an internal view switcher —
	// rewrite old links the same way as sessions→llm above.
	$: if ($page.url.searchParams.get('tab') === 'attributes') {
		const url = new URL($page.url);
		url.searchParams.set('tab', 'models');
		url.searchParams.set('view', 'attributes');
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	$: {
		const params = $page.url.searchParams;
		const rawTab = params.get('tab') || 'settings';
		const requestedTab =
			rawTab === 'sessions'
				? 'llm'
				: rawTab === 'groups'
					? 'users'
					: rawTab === 'attributes'
						? 'models'
						: rawTab;
		// Stats and Documentation live outside the `tabs` array (they're
		// informative views, not management tabs — surfaced as their own
		// controls on the right of the tab bar), so they need an explicit
		// carve-out here or ?tab=stats / ?tab=docs would 404 into the settings
		// redirect below.
		const isKnownTab =
			requestedTab === 'stats' ||
			requestedTab === 'docs' ||
			tabs.some((tab) => tab.id === requestedTab) ||
			pluginTabs.some((tab) => tab.id === requestedTab);
		activeTab = isKnownTab ? requestedTab : 'settings';
		initialDocId = activeTab === 'docs' ? params.get('doc') : null;
	}

	onMount(() => {
		loading = false;
	});

	// Redirect if not authenticated or not admin
	$: {
		if (!authLoading && !loading) {
			if (!isAuthenticated) {
				goto('/login');
			} else if (currentUser && currentUser.account_type !== 'ADMIN') {
				goto('/generate');
			}
		}
	}

	const tabs = [
		{ id: 'settings', label: 'System Settings', icon: 'cog' },
		{ id: 'models', label: 'Models', icon: 'database' },
		{ id: 'presets', label: 'Presets', icon: 'cube' },
		{ id: 'backends', label: 'Backends', icon: 'server' },
		{ id: 'generations', label: 'Generations', icon: 'document' },
		{ id: 'users', label: 'Users', icon: 'userGroup' },
		{ id: 'llm', label: 'LLM / Assistant', icon: 'chip' },
		{ id: 'plugins', label: 'Plugins', icon: 'puzzle' },
		{ id: 'downloads', label: 'Downloads', icon: 'download' },
		{ id: 'automations', label: 'Automations', icon: 'bolt' }
	];

	const tabLoaders: Record<string, () => Promise<{ default: any }>> = {
		settings: () => import('./components/SystemSettingsTab.svelte'),
		stats: () => import('./components/StatsTab.svelte'),
		models: () => import('./components/ModelsTab.svelte'),
		presets: () => import('./components/PresetsTab.svelte'),
		backends: () => import('./components/BackendsTab.svelte'),
		generations: () => import('./components/GenerationsTab.svelte'),
		users: () => import('./components/UsersGroupsTab.svelte'),
		llm: () => import('./components/LLMAssistantTab.svelte'),
		plugins: () => import('./components/PluginsTab.svelte'),
		downloads: () => import('./components/DownloaderTab.svelte'),
		automations: () => import('./components/AutomationsTab.svelte'),
		docs: () => import('./components/DocumentationTab.svelte')
	};

	function tabHref(tabId: string): string {
		const params = new URLSearchParams($page.url.searchParams);
		params.set('tab', tabId);
		if (tabId !== 'docs') params.delete('doc');

		const query = params.toString();
		return `${$page.url.pathname}${query ? `?${query}` : ''}`;
	}

	function getIconPath(icon: string): string {
		const icons: Record<string, string> = {
			database: 'M4 7v10c0 2 1 3 3 3h10c2 0 3-1 3-3V7c0-2-1-3-3-3H7C5 4 4 5 4 7z M9 11h6',
			cog: 'M12 15a3 3 0 100-6 3 3 0 000 6z M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z',
			cube: 'M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z M3.27 6.96L12 12.01l8.73-5.05 M12 22.08V12',
			users: 'M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2 M9 11a4 4 0 100-8 4 4 0 000 8z M23 21v-2a4 4 0 00-3-3.87 M16 3.13a4 4 0 010 7.75',
			userGroup: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z',
			chip: 'M12 16a4 4 0 100-8 4 4 0 000 8z M6 9h1 M17 9h1 M6 15h1 M17 15h1 M9 6v1 M9 17v1 M15 6v1 M15 17v1',
			chatSessions: 'M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z',
			puzzle: 'M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z',
			code: 'M16 18l6-6-6-6 M8 6l-6 6 6 6',
			server: 'M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01',
			bolt: 'M13 2L3 14h7l-1 8 10-12h-7l1-8z',
			download: 'M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4 M7 10l5 5 5-5 M12 15V3',
			book: 'M4 19.5A2.5 2.5 0 016.5 17H20 M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z',
			chart: 'M3 3v18h18 M18 17V9 M13 17V5 M8 17v-4',
			gauge: 'M3.34 19a10 10 0 1 1 17.32 0 M12 14l4-4',
			document: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
			sliders: 'M4 21v-7 M4 10V3 M12 21v-9 M12 8V3 M20 21v-5 M20 12V3 M1 14h6 M9 8h6 M17 16h6'
		};
		return icons[icon] || icons.cog;
	}
</script>

{#if authLoading || loading}
	<div class="min-h-screen flex items-center justify-center bg-canvas">
		<div class="text-center">
			<div
				class="animate-spin rounded-full h-12 w-12 border-b-2 border-line-hover mx-auto mb-4"
			></div>
			<p class="text-fg-muted">Loading authentication...</p>
		</div>
	</div>
{:else if currentUser && currentUser.account_type !== 'ADMIN'}
	<div class="min-h-screen bg-canvas">
		<div class="py-8 px-6">
			<div class="max-w-[1600px] mx-auto">
				<div class="text-center py-16">
					<svg
						class="w-16 h-16 text-fg-subtle mx-auto mb-4"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
						></path>
					</svg>
					<h2 class="text-xl font-semibold text-fg mb-2">Access Denied</h2>
					<p class="text-fg-muted">
						You need administrator privileges to access this page.
					</p>
				</div>
			</div>
		</div>
	</div>
{:else}
	<div class="min-h-screen bg-canvas">
		<!-- Top Bar with Title and Tabs -->
		<PageHeader sticky={false}>
			<div class="flex items-center gap-6 w-full">
				<!-- Page Title -->
				<div class="flex items-center gap-3 flex-shrink-0">
					<svg
						class="w-4 h-4 text-fg-muted"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
						></path>
					</svg>
					<span class="text-xs font-semibold text-fg uppercase tracking-wide">Admin</span>
				</div>

				<div class="h-6 w-px bg-line-strong"></div>

				<!-- Tab Navigation - inline -->
				<nav class="flex items-center gap-1 flex-1 overflow-x-auto">
					{#each tabs as tab}
						<a
							href={tabHref(tab.id)}
							class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap {activeTab === tab.id
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
							aria-current={activeTab === tab.id ? 'page' : undefined}
						>
							<svg
								class="w-3.5 h-3.5"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d={getIconPath(tab.icon)}
								></path>
							</svg>
							{tab.label}
						</a>
					{/each}
					<!-- Plugin-contributed tabs (A5 admin.tabs extension slot) -->
					{#each pluginTabs as tab (tab.id)}
						<a
							href={tabHref(tab.id)}
							class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap {activeTab === tab.id
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
							aria-current={activeTab === tab.id ? 'page' : undefined}
						>
							<svg
								class="w-3.5 h-3.5"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d={getIconPath(tab.icon)}
								></path>
							</svg>
							{tab.label}
						</a>
					{/each}

					<!-- Spacer pushes the informative group (Documentation, Stats) to
					     the right of the management tabs; neither is a management
					     tab, so the pair is set off by one shared divider rather
					     than living in `tabs`. Stays inside the scrollable row so
					     it's still reachable (not clipped) when the bar overflows
					     on mobile. -->
					<div class="flex-1 min-w-4"></div>
					<div class="h-5 w-px bg-line-strong flex-shrink-0"></div>
					<a
						href={tabHref('docs')}
						class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap flex-shrink-0 {activeTab ===
						'docs'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
						aria-current={activeTab === 'docs' ? 'page' : undefined}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d={getIconPath('document')}
							></path>
						</svg>
						Documentation
					</a>
					<a
						href={tabHref('stats')}
						class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap flex-shrink-0 {activeTab ===
						'stats'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
						aria-current={activeTab === 'stats' ? 'page' : undefined}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d={getIconPath('gauge')}
							></path>
						</svg>
						Stats
					</a>
				</nav>
			</div>
		</PageHeader>

		<!-- Tab Content -->
		<div class="px-4 sm:px-6 py-4 sm:py-6">
			{#if tabLoaders[activeTab]}
				{#key activeTab}
					<LazyAdminTab
						loader={tabLoaders[activeTab]}
						componentProps={activeTab === 'users'
							? { currentUser }
							: activeTab === 'docs'
								? { initialDocId }
								: {}}
					/>
				{/key}
			{:else if pluginTabs.some((t) => t.id === activeTab)}
				{@const activePluginTab = pluginTabs.find((t) => t.id === activeTab)}
				{#if activePluginTab}
					{#await resolvePluginComponent(activePluginTab.contribution.plugin_id, activePluginTab.contribution.component) then Component}
						{#if Component}
							<svelte:component this={Component} />
						{/if}
					{/await}
				{/if}
			{/if}
		</div>
	</div>
{/if}
