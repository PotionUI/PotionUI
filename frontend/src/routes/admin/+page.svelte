<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth';
	import { PageHeader } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import LazyAdminTab from './components/LazyAdminTab.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
	import { contributionsForSlot } from '$lib/extensions/extensionSlots';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import { adminTabHref } from './adminTabHref';
	import { adminSectionIcon, ADMIN_PLUGIN_TAB_FALLBACK_ICON } from './adminSections';

	$: adminTabContributions = contributionsForSlot('admin.tabs');
	$: pluginTabs = $adminTabContributions.map((c) => ({
		id: `plugin:${c.plugin_id}:${c.component}`,
		label: c.label || c.component,
		icon: c.icon || ADMIN_PLUGIN_TAB_FALLBACK_ICON,
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
		{ id: 'settings', label: 'System Settings' },
		{ id: 'models', label: 'Models' },
		{ id: 'presets', label: 'Presets' },
		{ id: 'recipes', label: 'Recipes' },
		{ id: 'backends', label: 'Backends' },
		{ id: 'generations', label: 'Generations' },
		{ id: 'users', label: 'Users' },
		{ id: 'llm', label: 'LLM / Assistant' },
		{ id: 'plugins', label: 'Plugins' },
		{ id: 'downloads', label: 'Downloads' },
		{ id: 'automations', label: 'Automations' }
	];

	const tabLoaders: Record<string, () => Promise<{ default: any }>> = {
		settings: () => import('./components/SystemSettingsTab.svelte'),
		stats: () => import('./components/StatsTab.svelte'),
		models: () => import('./components/ModelsTab.svelte'),
		presets: () => import('./components/PresetsTab.svelte'),
		recipes: () => import('./components/RecipesTab.svelte'),
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
		return adminTabHref($page.url.pathname, tabId);
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
	<div class="flex h-[100dvh] flex-col overflow-hidden bg-canvas">
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
							<Icon name={adminSectionIcon(tab.id)} className="w-3.5 h-3.5" />
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
							<Icon name={tab.icon} className="w-3.5 h-3.5" />
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
						<Icon name={adminSectionIcon('docs')} className="w-3.5 h-3.5" />
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
						<Icon name={adminSectionIcon('stats')} className="w-3.5 h-3.5" />
						Stats
					</a>
				</nav>
			</div>
		</PageHeader>

		<div class="min-h-0 flex-1">
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
					{@const pluginTabSections = [
						{ id: 'all', label: activePluginTab.label, icon: activePluginTab.icon }
					] as LibrarySectionMeta<'all'>[]}
					<LibraryShell
						title={activePluginTab.label}
						persistKey="admin-plugin-tab-{activePluginTab.contribution.plugin_id}-{activePluginTab.contribution.component}"
						heightClass="h-full"
						sections={pluginTabSections}
						section="all"
						onSelectSection={() => {}}
					>
						{#await resolvePluginComponent(activePluginTab.contribution.plugin_id, activePluginTab.contribution.component) then Component}
							{#if Component}
								<div class="min-h-full bg-surface-2">
									<svelte:component this={Component} />
								</div>
							{/if}
						{/await}
					</LibraryShell>
				{/if}
			{/if}
		</div>
	</div>
{/if}
