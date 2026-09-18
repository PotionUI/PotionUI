<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth';
	import { keybindingsStore, shortcutLabels } from '$lib/stores/keybindings';
	import { chatPanelStore, isChatPanelOpen } from '$lib/stores/chatPanel';
	import { chatUnread } from '$lib/stores/chatUnread';
	import { notifications, bellIndicator } from '$lib/stores/notifications';
	import { pluginNavItems } from '$lib/stores/plugins';
	import { iconPaths } from '$lib/utils/IconLibrary';
	import { MENU_EDGE_GUTTER, MENU_GAP } from '$lib/utils/menuPosition';
	import { partitionNavItems } from './sidebarOverflow';
	import portal from '$lib/actions/portal';
	import Tooltip from './Tooltip.svelte';
	import Logo from './brand/Logo.svelte';
	import SidebarWidgets from './SidebarWidgets.svelte';
	import QuickActions from './QuickActions.svelte';
	import UserMenu from './UserMenu.svelte';
	import { contributionsForSlot } from '$lib/extensions/extensionSlots';
	import { api } from '$lib/services/api/index';
	import { setupCompletionPing } from '$lib/stores/setupCompletion';

	// `nav.primary` extension slot contributions, merged into the core nav
	// list below by `order` (core items get implicit orders 10, 20, 30... so
	// a contribution can slot in anywhere via its own `order`). Role-filtered
	// by `contributionsForSlot` the same way `visiblePluginNavItems` filters
	// the older per-page plugin nav mechanism below (kept separate/untouched).
	$: navPrimaryContributions = contributionsForSlot('nav.primary');

	// Reactively check if user is admin
	$: showAdminSection = $authStore.user?.account_type === 'ADMIN';

	// "Resume setup" - fetched once per session, the moment we know the user
	// is an admin (not in onMount, since `$authStore.user` populates async
	// after the sidebar mounts). Deliberately a single fetch, not a poll: this
	// is a nudge to finish setup, not a live health monitor.
	let readinessChecked = false;
	let showResumeSetup = false;

	function checkReadiness() {
		api
			.getReadiness()
			.then((report) => {
				showResumeSetup = report.overall !== 'ready';
			})
			.catch(() => {
				// Fail-soft: a readiness-check failure must never break the sidebar.
				showResumeSetup = false;
			});
	}

	$: if (showAdminSection && !readinessChecked) {
		readinessChecked = true;
		checkReadiness();
	}

	// Re-check the instant a guided setup run completes (see
	// `notifySetupCompleted`), rather than waiting on the next full page
	// load, so the nudge clears itself as soon as it's stale.
	$: if (showAdminSection && readinessChecked && $setupCompletionPing > 0) checkReadiness();

	$: bell = bellIndicator($notifications);

	// Filter plugin nav items by require_role
	$: visiblePluginNavItems = ($pluginNavItems || []).filter(item =>
		!item.require_role || item.require_role === $authStore.user?.account_type
	);

	interface NavItem {
		path: string;
		label: string;
		icon: string;
		order?: number;
		// Keybinding actionId bound to this route (e.g. `go_generate`), used to
		// look up a shortcut chip in `shortcutLabels`.
		actionId?: string;
	}

	// Implicit orders 10, 20, 30... leave room for `nav.primary` contributions
	// to slot in anywhere (e.g. order: 15 lands between Generate and History).
	const navItems: NavItem[] = [
		{ path: '/generate', label: 'Generate', icon: 'sparkles', order: 10, actionId: 'go_generate' },
		{ path: '/history', label: 'History', icon: 'clock', order: 20, actionId: 'go_history' },
		{ path: '/library', label: 'Library', icon: 'photo', order: 25, actionId: 'go_library' },
		{ path: '/models', label: 'Models', icon: 'cube', order: 30, actionId: 'go_models' },
		{ path: '/phrasebook', label: 'Phrasebook', icon: 'hash', order: 60, actionId: 'go_phrasebook' },
		{ path: '/prompts', label: 'Prompts', icon: 'document', order: 70, actionId: 'go_prompts' },
		{ path: '/inspirations', label: 'Inspirations', icon: 'lightbulb', order: 80, actionId: 'go_inspirations' }
	];

	// Core nav merged with `nav.primary` extension slot contributions, ordered together.
	$: mergedNavItems = [
		...navItems,
		...$navPrimaryContributions.map(
			(c): NavItem => ({
				path: c.route || `/plugins/${c.component}`,
				label: c.label || c.component,
				icon: c.icon || 'puzzle',
				order: c.order
			})
		)
	].sort((a, b) => (a.order ?? 100) - (b.order ?? 100));

	const adminNavItems: NavItem[] = [
		{ path: '/admin', label: 'Administration', icon: 'shield' }
	];

	// "Resume setup" slots in ahead of Administration while readiness isn't
	// fully green - it's the more urgent of the two for an admin to notice.
	$: computedAdminNavItems = showResumeSetup
		? [{ path: '/setup', label: 'Resume setup', icon: 'gauge' } as NavItem, ...adminNavItems]
		: adminNavItems;

	// Reactive statement to track current path and force re-evaluation
	$: currentPath = $page.url.pathname;

	// Reactive function to check if a path is active
	$: isActive = (itemPath: string): boolean => {
		return currentPath === itemPath || currentPath.startsWith(itemPath + '/');
	};

	/** Return the first path string for a given icon name from the centralized library. */
	function iconPath(name: string): string {
		const p = iconPaths[name];
		return Array.isArray(p) ? p[0] : (p ?? '');
	}

	/** Return all path strings for a given icon name (for multi-path icons). */
	function iconPathList(name: string): string[] {
		const p = iconPaths[name];
		return Array.isArray(p) ? p : p ? [p] : [];
	}

	interface OverflowableNavItem {
		path: string;
		label: string;
		iconD: string;
		kind: 'core' | 'plugin';
		actionId?: string;
	}

	$: overflowableNavItems = [
		...mergedNavItems.map(
			(item): OverflowableNavItem => ({
				path: item.path,
				label: item.label,
				iconD: iconPath(item.icon),
				kind: 'core',
				actionId: item.actionId
			})
		),
		...visiblePluginNavItems.map(
			(item): OverflowableNavItem => ({
				path: `/plugins/${item.route}`,
				label: item.label,
				iconD: item.icon_svg || 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z',
				kind: 'plugin'
			})
		)
	];

	const NAV_ROW_HEIGHT = 44;
	const NAV_DIVIDER_HEIGHT = 17;
	const NAV_VERTICAL_PADDING = 32;

	let navEl: HTMLElement;
	let navHeight = 0;

	$: availableNavHeight = Math.max(0, navHeight - NAV_VERTICAL_PADDING);

	$: adminReservedHeight = showAdminSection
		? NAV_DIVIDER_HEIGHT + computedAdminNavItems.length * NAV_ROW_HEIGHT
		: 0;

	$: navPartition = partitionNavItems(overflowableNavItems, availableNavHeight, {
		rowHeight: NAV_ROW_HEIGHT,
		reservedHeight: adminReservedHeight
	});

	$: visibleCoreNavItems = navPartition.visible.filter((item) => item.kind === 'core');
	$: visiblePluginPageItems = navPartition.visible.filter((item) => item.kind === 'plugin');
	$: overflowNavItems = navPartition.overflow;
	$: isMoreActive = overflowNavItems.some((item) => isActive(item.path));

	let moreOpen = false;
	let moreTriggerEl: HTMLButtonElement;
	let moreMenuEl: HTMLDivElement;
	let morePos = { top: 0, left: 0 };

	function updateMorePosition() {
		if (!moreTriggerEl) return;
		const rect = moreTriggerEl.getBoundingClientRect();
		const menuHeight = moreMenuEl?.getBoundingClientRect().height ?? overflowNavItems.length * 36 + 8;
		let top = rect.top;
		const maxTop = window.innerHeight - menuHeight - MENU_EDGE_GUTTER;
		if (top > maxTop) top = maxTop;
		if (top < MENU_EDGE_GUTTER) top = MENU_EDGE_GUTTER;
		morePos = { top, left: rect.right + MENU_GAP };
	}

	function toggleMore() {
		moreOpen = !moreOpen;
		if (moreOpen) {
			updateMorePosition();
			requestAnimationFrame(updateMorePosition);
		}
	}

	function closeMore() {
		moreOpen = false;
	}

	function handleMoreClickOutside(event: MouseEvent) {
		const target = event.target as Node;
		if (
			moreOpen &&
			moreTriggerEl &&
			!moreTriggerEl.contains(target) &&
			!(moreMenuEl && moreMenuEl.contains(target))
		) {
			closeMore();
		}
	}

	function handleMoreKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && moreOpen) closeMore();
	}

	onMount(() => {
		document.addEventListener('click', handleMoreClickOutside);
		document.addEventListener('keydown', handleMoreKeydown);
		return () => {
			document.removeEventListener('click', handleMoreClickOutside);
			document.removeEventListener('keydown', handleMoreKeydown);
		};
	});
</script>

<aside class="fixed left-0 top-0 h-screen w-14 flex flex-col bg-canvas border-r border-line z-50">
	<!-- Logo/Home -->
	<div class="h-header flex items-center justify-center border-b border-line">
		<Tooltip text="PotionUI" position="right">
			<a
				href="/"
				class="p-0.5 rounded-xl text-fg hover:bg-surface-2 transition-colors"
				aria-label="PotionUI Home"
			>
				<Logo size={38} />
			</a>
		</Tooltip>
	</div>

	<!-- Main Navigation -->
	<nav bind:this={navEl} bind:clientHeight={navHeight} class="flex-1 py-4 overflow-hidden">
		<div class="space-y-1 px-2">
			{#each visibleCoreNavItems as item}
				<Tooltip
					text={item.label}
					kbd={item.actionId ? $shortcutLabels[item.actionId] : undefined}
					position="right"
					wrapperClass="flex justify-center"
				>
					<a
						href={item.path}
						class="relative flex items-center justify-center w-10 h-10 rounded-lg transition-all
							{isActive(item.path)
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
						aria-label={item.label}
					>
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d={item.iconD}
							/>
						</svg>
						<!-- Active indicator -->
						{#if isActive(item.path)}
							<div class="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-signal rounded-r"></div>
						{/if}
					</a>
				</Tooltip>
			{/each}

			<!-- Plugin Pages Section -->
			{#if visiblePluginPageItems.length > 0}
				{#if visibleCoreNavItems.length > 0}
					<div class="my-2 mx-2 border-t border-line"></div>
				{/if}
				{#each visiblePluginPageItems as item}
					<Tooltip text={item.label} position="right" wrapperClass="flex justify-center">
						<a
							href={item.path}
							class="relative flex items-center justify-center w-10 h-10 rounded-lg transition-all
								{isActive(item.path)
									? 'bg-signal/10 text-signal'
									: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
							aria-label={item.label}
						>
							<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d={item.iconD}
								/>
							</svg>
							{#if isActive(item.path)}
								<div class="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-signal rounded-r"></div>
							{/if}
						</a>
					</Tooltip>
				{/each}
			{/if}

			{#if overflowNavItems.length > 0}
				<Tooltip text="More" position="right" wrapperClass="flex justify-center">
					<button
						bind:this={moreTriggerEl}
						type="button"
						on:click={toggleMore}
						class="relative flex items-center justify-center w-10 h-10 rounded-lg transition-all
							{isMoreActive
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
						aria-label="More"
						aria-haspopup="menu"
						aria-expanded={moreOpen}
					>
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d={iconPath('more')}
							/>
						</svg>
						{#if isMoreActive}
							<div class="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-signal rounded-r"></div>
						{/if}
					</button>
				</Tooltip>
			{/if}

			<!-- Admin Section -->
			{#if showAdminSection}
				<div class="my-2 mx-2 border-t border-line"></div>
				{#each computedAdminNavItems as item}
					<Tooltip text={item.label} position="right" wrapperClass="flex justify-center">
						<a
							href={item.path}
							class="relative flex items-center justify-center w-10 h-10 rounded-lg transition-all
								{isActive(item.path)
									? 'bg-signal/10 text-signal'
									: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
							aria-label={item.label}
						>
							<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d={iconPath(item.icon)}
								/>
							</svg>
							<!-- Active indicator -->
							{#if isActive(item.path)}
								<div class="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-signal rounded-r"></div>
							{/if}
						</a>
					</Tooltip>
				{/each}
			{/if}
		</div>
	</nav>

	<!-- Bottom Section: Widgets + Quick Actions + User -->
	<div class="border-t border-line py-3 px-2 space-y-2">
		<!-- Dynamic Sidebar Widgets (e.g. System Monitor plugin) -->
		<SidebarWidgets position="bottom" />

		<!-- Divider between widgets and quick actions -->
		<div class="my-1 mx-2 border-t border-line"></div>

		<!-- AI Chat — muted like the rest of the rail, marked out only by a
			static iridescent gradient border (the AI-product ring idiom);
			signal ring + icon color mark it open. -->
		<div class="flex flex-col items-center">
			<Tooltip text="AI Chat" kbd={$shortcutLabels['open_chat']} position="right">
				<button
					type="button"
					on:click={() => chatPanelStore.toggle()}
					class="ai-chat-trigger relative w-8 h-8 flex items-center justify-center rounded-lg transition-all
						{$isChatPanelOpen
							? 'ai-chat-trigger--open text-fg'
							: 'text-fg-muted hover:text-fg'}"
					aria-label={$chatUnread ? 'AI Chat, unread reply' : 'AI Chat'}
					aria-pressed={$isChatPanelOpen}
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						{#each iconPathList('chat') as d}
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" {d} />
						{/each}
					</svg>
					{#if $chatUnread}
						<span class="absolute top-0 right-0 w-2 h-2 rounded-full bg-signal" aria-hidden="true"></span>
					{/if}
				</button>
			</Tooltip>
		</div>

		<div class="flex flex-col items-center">
			<Tooltip text="Notifications" position="right">
				<button
					type="button"
					on:click={() => notifications.togglePanel()}
					class="relative w-8 h-8 flex items-center justify-center rounded-lg transition-all
						{$notifications.panelOpen
							? 'bg-surface-2 text-fg'
							: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
					aria-label={bell.kind !== 'idle' ? 'Notifications, unread' : 'Notifications'}
					aria-pressed={$notifications.panelOpen}
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={iconPath('bell')} />
					</svg>
					{#if bell.kind === 'count'}
						<span
							class="absolute -top-1 -right-1 min-w-[15px] h-[15px] px-1 rounded bg-signal-solid text-white text-2xs font-mono font-semibold tabular-nums flex items-center justify-center leading-none"
							aria-hidden="true"
						>
							{bell.count}
						</span>
					{:else if bell.kind === 'dot'}
						<span class="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-signal" aria-hidden="true"></span>
					{/if}
				</button>
			</Tooltip>
		</div>

		<!-- Keyboard Shortcuts -->
		<div class="flex flex-col items-center">
			<Tooltip text="Keyboard shortcuts" kbd={$shortcutLabels['show_help']} position="right">
				<button
					type="button"
					on:click={() => keybindingsStore.openHelp()}
					class="w-8 h-8 flex items-center justify-center rounded-lg text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors"
					aria-label="Keyboard shortcuts"
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						{#each iconPathList('keyboard') as d}
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" {d} />
						{/each}
					</svg>
				</button>
			</Tooltip>
		</div>

		<!-- Quick Actions (core palette; plugins are one contributing source) -->
		<QuickActions />

		{#if $authStore.user}
			<div class="flex flex-col items-center pt-2 border-t border-line">
				<UserMenu />
			</div>
		{/if}
	</div>
</aside>

{#if moreOpen}
	<div
		use:portal
		bind:this={moreMenuEl}
		class="fixed z-[9999] min-w-[12rem] p-1 bg-surface-1 border border-line-strong rounded-lg shadow-overlay"
		style="top: {morePos.top}px; left: {morePos.left}px;"
		role="menu"
		aria-label="More"
	>
		{#each overflowNavItems as item}
			<a
				href={item.path}
				role="menuitem"
				on:click={closeMore}
				class="flex items-center gap-2.5 min-h-[36px] px-2.5 py-2 rounded text-sm transition-colors
					{isActive(item.path)
						? 'bg-signal/10 text-signal'
						: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
			>
				<svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={item.iconD} />
				</svg>
				<span class="truncate">{item.label}</span>
			</a>
		{/each}
	</div>
{/if}

<style>
	/* AI Chat trigger — the sole "AI-product gradient ring" idiom in the app,
	   scoped to this one button. Border-box gets the iridescent gradient,
	   padding-box gets a flat fill so the inside reads as a normal rail icon. */
	.ai-chat-trigger {
		border: 1px solid transparent;
		background:
			linear-gradient(rgb(var(--canvas)), rgb(var(--canvas))) padding-box,
			linear-gradient(135deg, rgb(var(--ai-1)), rgb(var(--ai-2)), rgb(var(--ai-3))) border-box;
	}

	.ai-chat-trigger:hover:not(.ai-chat-trigger--open) {
		background:
			linear-gradient(rgb(var(--surface-2)), rgb(var(--surface-2))) padding-box,
			linear-gradient(135deg, rgb(var(--ai-1)), rgb(var(--ai-2)), rgb(var(--ai-3))) border-box;
		box-shadow:
			0 0 10px rgb(var(--ai-2) / 0.35),
			0 0 3px rgb(var(--ai-1) / 0.3);
	}

	/* Registered so the conic angle interpolates; without @property the
	   animation would snap instead of spin. Registration is page-global but
	   the property is only ever set here. */
	@property --ai-angle {
		syntax: '<angle>';
		initial-value: 0deg;
		inherits: false;
	}

	.ai-chat-trigger--open {
		border-width: 2px;
		background:
			linear-gradient(rgb(var(--surface-2)), rgb(var(--surface-2))) padding-box,
			conic-gradient(
					from var(--ai-angle),
					rgb(var(--ai-1)),
					rgb(var(--ai-2)),
					rgb(var(--ai-3)),
					rgb(var(--ai-1))
				)
				border-box;
		box-shadow:
			0 0 12px rgb(var(--ai-2) / 0.4),
			0 0 4px rgb(var(--ai-1) / 0.35);
		animation: ai-ring-spin 3s linear infinite;
	}

	@keyframes ai-ring-spin {
		to {
			--ai-angle: 360deg;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.ai-chat-trigger--open {
			animation: none;
		}
	}
</style>
