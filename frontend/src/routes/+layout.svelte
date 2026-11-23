<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto, afterNavigate } from '$app/navigation';
	import { browser } from '$app/environment';
	import { storage } from '$lib/utils/storage';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth';
	import { installLimitsWatcher, refreshLimits, resetLimitsState } from '$lib/plans/store';
	import { keybindingsStore } from '$lib/stores/keybindings';
	import { chatPanelStore, isChatPanelOpen } from '$lib/stores/chatPanel';
	import { isMobile } from '$lib/stores/viewport';
	import { init as initKeyboard, destroy as destroyKeyboard } from '$lib/services/keyboard';
	import { registerLazyComponent } from '$lib/plugin-api/componentRegistry';
	import { whenIdle } from '$lib/utils/idle';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { modeInstallRequest } from '$lib/stores/modeInstall';
	import { isHelpOpen } from '$lib/stores/keybindings';
	import { loadPresets } from '$lib/stores/presetsCatalog';
	import { loadModeLabels } from '$lib/utils/modeLabel.svelte';
	import { canInstall, initPwaInstall, promptInstall } from '$lib/stores/pwaInstall';
	import ToastContainer from '$lib/components/ToastContainer.svelte';
	import AddToKitHost from '$lib/components/kits/AddToKitHost.svelte';
	import LibraryModalHost from '$lib/components/library/LibraryModalHost.svelte';
	import ToolRunChip from '$lib/components/tools/ToolRunChip.svelte';
	import ConfirmHost from '$lib/components/modals/ConfirmHost.svelte';
	import AccountSwitchOverlay from '$lib/components/accounts/AccountSwitchOverlay.svelte';
	import { initCrossTab } from '$lib/stores/accounts';
	import { notifications } from '$lib/stores/notifications';
	import { tabDraftSync } from '$lib/stores/tabDraftSync';
	import { sessionLiveSync } from '$lib/stores/sessionLiveSync';
	import { notificationsWebSocket } from '$lib/services/notificationsWebsocket';
	import { themeStore } from '$lib/stores/theme';
	import { printStartupBanner } from '$lib/utils/startupBanner';
	import { installInputModality } from '$lib/utils/inputModality';
	import { resolveAuthLayoutMode, shouldRedirectToLogin } from '$lib/utils/authLayout';
	import { loginUrlWithReturn } from '$lib/utils/returnPath';
	import { Spinner, IconButton } from '$lib/components/ui';
	import '../app.css';

	let mounted = false;
	let keyboardInitialized = false;
	let notificationsInitialized = false;
	let cancelNotificationsIdle: (() => void) | null = null;
	let authenticatedRuntimeInitialized = false;
	let authenticatedRuntimeReady = false;
	let installDismissed = false;
	let SidebarComponent: any = null;
	let MobileTabBarComponent: any = null;
	let GlobalChatPanelComponent: any = null;
	let ChatSheetComponent: any = null;
	let KeyboardShortcutsModalComponent: any = null;
	let NotificationPanelComponent: any = null;
	let ModeInstallHostComponent: any = null;
	let IndexingChoiceModalComponent: any = null;

	const loadSidebar = () => import('$lib/components/Sidebar.svelte');
	const loadMobileTabBar = () => import('$lib/components/MobileTabBar.svelte');
	const loadHostApi = () => import('$lib/plugin-api/host');
	const loadExtensionRefresh = () => import('$lib/plugin-api/extensionRefresh');
	const loadBuiltinFields = () => import('$lib/fields/builtin');

	if (browser && storage.get('auth_token')) {
		void loadSidebar();
		void loadMobileTabBar();
		void loadHostApi();
		void loadBuiltinFields();
		void import('$lib/generation/prepareRequest');
		void loadExtensionRefresh().then((module) => module.prefetchExtensionSnapshot());
	}

	const pendingLoads = new Set<string>();

	function loadLazy(key: string, label: string, load: () => Promise<{ default: any }>, assign: (component: any) => void, onFail?: () => void, notify = true) {
		if (pendingLoads.has(key)) return;
		pendingLoads.add(key);
		load()
			.then((module) => assign(module.default))
			.catch((error) => {
				logger.error(`Failed to load ${label}:`, error);
				if (notify) toasts.error(`Could not load ${label}. Try again.`);
				onFail?.();
			})
			.finally(() => pendingLoads.delete(key));
	}

	onMount(() => {
		mounted = true;
		const stopCrossTab = initCrossTab();
		const stopInputModality = installInputModality();
		printStartupBanner();
		themeStore.init();
		initPwaInstall();
		installDismissed = storage.get('potionui-install-dismissed') === '1';
		return () => {
			stopCrossTab();
			stopInputModality();
		};
	});

	function dismissInstallPrompt() {
		installDismissed = true;
		storage.set('potionui-install-dismissed', '1');
	}

	async function initializeAuthenticatedRuntime() {
		if (authenticatedRuntimeInitialized) return;
		authenticatedRuntimeInitialized = true;

		// Fire-and-forget so callers (Generate page, chat context lookups) find
		// this already in flight or resolved; never awaited, so a slow presets
		// fetch can't delay the shell, and the failure is swallowed here since
		// each caller handles its own.
		loadPresets().catch(() => {});

		void loadSidebar().then((module) => (SidebarComponent = module.default));
		void loadMobileTabBar().then((module) => (MobileTabBarComponent = module.default));
		whenIdle(() => {
			ensureKeyboardModal(false);
			ensureNotificationPanel(false);
			ensureChatPanel(false);
		});

		registerLazyComponent('GenerationHistoryModal', () => import('$lib/components/modals/GenerationHistoryModal.svelte'));
		registerLazyComponent('TextInput', () => import('$lib/components/form-fields/TextInput.svelte'));
		registerLazyComponent('SelectField', () => import('$lib/components/form-fields/SelectField.svelte'));
		registerLazyComponent('CheckboxField', () => import('$lib/components/form-fields/CheckboxField.svelte'));
		registerLazyComponent('NumberInput', () => import('$lib/components/form-fields/NumberInput.svelte'));
		registerLazyComponent('MediaLoaderField', () => import('$lib/components/form-fields/MediaLoaderField.svelte'));
		registerLazyComponent('ModelField', () => import('$lib/components/form-fields/ModelField.svelte'));
		registerLazyComponent('PromptModelField', () => import('./prompts/components/PromptModelField.svelte'));
		registerLazyComponent('UserPicker', () => import('$lib/components/ui/UserPicker.svelte'));
		registerLazyComponent('PluginModal', () => import('$lib/components/modals/PluginModal.svelte'));

		const [hostApi, builtinFields, extensionRefresh] = await Promise.all([
			loadHostApi(),
			loadBuiltinFields(),
			loadExtensionRefresh()
		]);
		await hostApi.whenHostReady();
		hostApi.initHostApi();
		builtinFields.registerBuiltinFieldComponents();
		await extensionRefresh.refreshPluginExtensions();
		authenticatedRuntimeReady = true;
	}

	function ensureKeyboardModal(notify = true) {
		if (!KeyboardShortcutsModalComponent) {
			loadLazy('keyboard-modal', 'the shortcuts', () => import('$lib/components/KeyboardShortcutsModal.svelte'), (component) => (KeyboardShortcutsModalComponent = component), undefined, notify);
		}
	}

	function ensureNotificationPanel(notify = true) {
		if (!NotificationPanelComponent) {
			loadLazy('notification-panel', 'notifications', () => import('$lib/components/notifications/NotificationPanel.svelte'), (component) => (NotificationPanelComponent = component), undefined, notify);
		}
	}

	function ensureChatPanel(notify = true) {
		if ($isMobile) {
			if (!ChatSheetComponent) {
				loadLazy('chat-sheet', 'the chat', () => import('$lib/components/ChatSheet.svelte'), (component) => (ChatSheetComponent = component), undefined, notify);
			}
		} else if (!GlobalChatPanelComponent) {
			loadLazy('chat-panel', 'the chat', () => import('$lib/components/GlobalChatPanel.svelte'), (component) => (GlobalChatPanelComponent = component), undefined, notify);
		}
	}

	function ensureModeInstallHost(notify = true) {
		if (!ModeInstallHostComponent) {
			loadLazy('mode-install', 'the install dialog', () => import('$lib/components/recipes/modeInstall/ModeInstallHost.svelte'), (component) => (ModeInstallHostComponent = component), undefined, notify);
		}
	}

	let indexingModalRequested = false;
	let cancelIndexingIdle: (() => void) | null = null;
	function requestIndexingModal() {
		if (indexingModalRequested) return;
		indexingModalRequested = true;
		cancelIndexingIdle = whenIdle(() => {
			loadLazy(
				'indexing-modal',
				'the indexing dialog',
				() => import('$lib/models-location/IndexingChoiceModal.svelte'),
				(component) => (IndexingChoiceModalComponent = component),
				() => (indexingModalRequested = false),
				false
			);
		});
	}

	$: isAdminAccount = $authStore.user?.account_type === 'ADMIN';
	$: if ($isHelpOpen) ensureKeyboardModal();
	$: if ($notifications.panelOpen) ensureNotificationPanel();
	$: if ($isChatPanelOpen && $isMobile !== undefined) ensureChatPanel();
	$: if ($modeInstallRequest) ensureModeInstallHost();
	$: if (isAdminAccount) requestIndexingModal();

	$: if (mounted && $authStore.isAuthenticated) {
		initializeAuthenticatedRuntime();
		void loadModeLabels();
	}

	// Initialize keyboard system once when authenticated, and drop the loaded
	// bindings back on logout (mirrors the notifications gating below) so a
	// different user signing in in the same tab re-fetches their own bindings
	// instead of keeping the previous user's custom overrides.
	$: if (mounted && $authStore.isAuthenticated && !keyboardInitialized) {
		keyboardInitialized = true;
		keybindingsStore.loadBindings().then(() => {
			initKeyboard();
			registerCoreHandlers();
		});
	}
	$: if (mounted && !$authStore.isAuthenticated && keyboardInitialized) {
		keyboardInitialized = false;
		keybindingsStore.reset();
	}

	// Load notifications + open the realtime channel once when authenticated,
	// and tear it back down on logout (mirrors the keyboard init gating above).
	$: if (mounted && $authStore.isAuthenticated && !notificationsInitialized) {
		notificationsInitialized = true;
		installLimitsWatcher();
		cancelNotificationsIdle?.();
		cancelNotificationsIdle = whenIdle(() => {
			cancelNotificationsIdle = null;
			notifications.load();
			notificationsWebSocket.connect();
			refreshLimits();
		}, 1000);
	}
	$: if (mounted && !$authStore.isAuthenticated && notificationsInitialized) {
		notificationsInitialized = false;
		cancelNotificationsIdle?.();
		cancelNotificationsIdle = null;
		notificationsWebSocket.disconnect();
		notifications.reset();
		resetLimitsState();
	}

	let tabDraftSyncStarted = false;
	let prefetchSettled = false;
	$: if (mounted && !$authStore.loading && !$authStore.isAuthenticated && !prefetchSettled) {
		prefetchSettled = true;
		void loadExtensionRefresh().then((module) => module.clearPrefetchedSnapshot());
	}
	$: if ($authStore.isAuthenticated) prefetchSettled = false;

	$: if (mounted && $authStore.isAuthenticated && !tabDraftSyncStarted) {
		tabDraftSyncStarted = true;
		tabDraftSync.start();
		sessionLiveSync.start();
	}
	$: if (mounted && !$authStore.isAuthenticated && tabDraftSyncStarted) {
		tabDraftSyncStarted = false;
		tabDraftSync.stop();
		sessionLiveSync.stop();
	}

	function registerCoreHandlers() {
		keybindingsStore.registerHandler('show_help', () => {
			keybindingsStore.toggleHelp();
		});
		keybindingsStore.registerHandler('open_chat', () => {
			chatPanelStore.toggle();
		});
		keybindingsStore.registerHandler('go_generate', () => {
			goto('/generate');
		});
		keybindingsStore.registerHandler('go_history', () => {
			goto('/history');
		});
		keybindingsStore.registerHandler('go_inspirations', () => {
			goto('/inspirations');
		});
		keybindingsStore.registerHandler('go_library', () => {
			goto('/library');
		});
		keybindingsStore.registerHandler('go_models', () => {
			goto('/models');
		});
		keybindingsStore.registerHandler('go_phrasebook', () => {
			goto('/phrasebook');
		});
		keybindingsStore.registerHandler('go_prompts', () => {
			goto('/prompts');
		});
	}

	onDestroy(() => {
		destroyKeyboard();
		cancelNotificationsIdle?.();
		cancelIndexingIdle?.();
		notificationsWebSocket.disconnect();
		themeStore.destroy();
	});

	// Determine if current route requires authentication. /setup/claim joins
	// login/register here - it's the pre-owner claim screen, so it gets the
	// same chrome-free, auth-agnostic treatment (see /setup/claim/+page.svelte).
	// /setup itself (the readiness home) is authenticated and keeps the normal
	// sidebar shell, so it is deliberately NOT listed here.
	$: isPublicRoute =
		$page.url.pathname === '/login' ||
		$page.url.pathname === '/register' ||
		$page.url.pathname === '/setup/claim';
	$: addingAccount =
		$page.url.pathname === '/login' &&
		$page.url.searchParams.get('add') === '1' &&
		$authStore.isAuthenticated;
	$: shouldShowNav = mounted && $authStore.isAuthenticated && !isPublicRoute;

	// Drives which branch of the shell markup below renders. Kept as a
	// dedicated derivation (rather than reusing shouldShowNav/isPublicRoute
	// inline) so the public/login branch never renders for an authenticated
	// user who is merely mid-redirect off a public route - see authLayout.ts.
	$: layoutMode = addingAccount && !$authStore.loading
		? 'public'
		: resolveAuthLayoutMode({
				mounted,
				loading: $authStore.loading,
				isAuthenticated: $authStore.isAuthenticated,
				isPublicRoute
			});

	// Reflect the unread count in the tab title, but only inside the authenticated
	// app shell — unauthenticated routes (login/docs) manage their own <title> via
	// svelte:head and we don't fight them. afterNavigate reapplies because a route's
	// own head can win right after navigation settles.
	function applyTabTitle(count: number, authed: boolean) {
		if (!browser || !authed) return;
		document.title = count > 0 ? `(${count}) PotionUI` : 'PotionUI';
	}
	$: applyTabTitle($notifications.unreadCount, shouldShowNav);
	afterNavigate(() => applyTabTitle($notifications.unreadCount, shouldShowNav));

	$: if (
		shouldRedirectToLogin({
			mounted,
			loading: $authStore.loading,
			isAuthenticated: $authStore.isAuthenticated,
			isPublicRoute,
			pathname: $page.url.pathname
		})
	) {
		goto(loginUrlWithReturn($page.url));
	}

	// Redirect to generate if authenticated and on public auth page
	$: if (mounted && !$authStore.loading && $authStore.isAuthenticated && isPublicRoute && !addingAccount) {
		goto('/generate');
	}
</script>

{#if layoutMode === 'boot' || layoutMode === 'redirecting'}
	<div class="min-h-screen flex items-center justify-center bg-canvas">
		<Spinner size="lg" />
	</div>
{:else if layoutMode === 'app'}
	<!-- Authenticated layout with sidebar -->
	<div class="min-h-screen bg-canvas">
			<div class="hidden md:block">
				{#if SidebarComponent}<svelte:component this={SidebarComponent} />{/if}
			</div>
		<main class="md:ml-14 min-h-dvh overflow-y-auto overflow-x-hidden pb-[var(--tab-bar-height)] md:pb-0">
			{#if authenticatedRuntimeReady}
				<slot />
			{:else}
				<div class="min-h-screen flex items-center justify-center">
					<div class="spinner"></div>
				</div>
			{/if}
		</main>
		{#if MobileTabBarComponent}<svelte:component this={MobileTabBarComponent} />{/if}
		{#if $canInstall && !installDismissed}
			<div
				class="fixed bottom-[calc(var(--tab-bar-height)_+_var(--tab-bar-lift,0px))] left-0 right-0 z-[var(--install-banner-z,30)] px-3 pb-2 md:hidden"
				data-install-banner
			>
				<div class="bg-surface-2 border border-line-strong rounded-xl p-3 flex items-center gap-3 shadow-floating">
					<div class="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center flex-shrink-0">
						<svg class="w-5 h-5 text-fg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
						</svg>
					</div>
					<div class="flex-1 min-w-0">
						<p class="text-sm font-medium text-fg">Install PotionUI</p>
						<p class="text-xs text-fg-muted">Add to your home screen</p>
					</div>
					<button
						on:click={() => promptInstall()}
						class="px-3 py-1.5 bg-accent text-accent-contrast text-xs font-semibold rounded hover:bg-accent-hover transition-colors flex-shrink-0"
					>
						Install
					</button>
					<IconButton icon="close" label="Dismiss install prompt" size="xs" onclick={dismissInstallPrompt} class="flex-shrink-0" />
				</div>
			</div>
		{/if}
		{#if !$isMobile && GlobalChatPanelComponent}<svelte:component this={GlobalChatPanelComponent} />{/if}
		{#if $isMobile && $isChatPanelOpen && ChatSheetComponent}<svelte:component this={ChatSheetComponent} />{/if}
		{#if KeyboardShortcutsModalComponent}<svelte:component this={KeyboardShortcutsModalComponent} />{/if}
		{#if NotificationPanelComponent}<svelte:component this={NotificationPanelComponent} />{/if}
		<LibraryModalHost />
		<AddToKitHost />
		<ToolRunChip />
		<ToastContainer />
		{#if ModeInstallHostComponent}<svelte:component this={ModeInstallHostComponent} />{/if}
		<ConfirmHost />
		{#if IndexingChoiceModalComponent}<svelte:component this={IndexingChoiceModalComponent} />{/if}
		<AccountSwitchOverlay />
	</div>
{:else}
	<!-- Public layout without nav (login page) -->
	<div class="min-h-screen bg-canvas">
		<slot />
		<ToastContainer />
		<ConfirmHost />
		<AccountSwitchOverlay />
	</div>
{/if}
