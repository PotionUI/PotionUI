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
	import { registerComponent } from '$lib/plugin-api/componentRegistry';
	import { initHostApi } from '$lib/plugin-api/host';
	import { refreshPluginExtensions } from '$lib/plugin-api/extensionRefresh';
	import { loadPresets } from '$lib/stores/presetsCatalog';
	import { loadModeLabels } from '$lib/utils/modeLabel.svelte';
	import { canInstall, initPwaInstall, promptInstall } from '$lib/stores/pwaInstall';
	import ToastContainer from '$lib/components/ToastContainer.svelte';
	import LibraryModalHost from '$lib/components/library/LibraryModalHost.svelte';
	import ToolRunChip from '$lib/components/tools/ToolRunChip.svelte';
	import ConfirmHost from '$lib/components/modals/ConfirmHost.svelte';
	import ModeInstallHost from '$lib/components/recipes/modeInstall/ModeInstallHost.svelte';
	import IndexingChoiceModal from '$lib/models-location/IndexingChoiceModal.svelte';
	import AccountSwitchOverlay from '$lib/components/accounts/AccountSwitchOverlay.svelte';
	import { initCrossTab } from '$lib/stores/accounts';
	import { notifications } from '$lib/stores/notifications';
	import { tabDraftSync } from '$lib/stores/tabDraftSync';
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
	let authenticatedRuntimeInitialized = false;
	let authenticatedRuntimeReady = false;
	let installDismissed = false;
	let SidebarComponent: any = null;
	let MobileTabBarComponent: any = null;
	let GlobalChatPanelComponent: any = null;
	let ChatSheetComponent: any = null;
	let KeyboardShortcutsModalComponent: any = null;
	let NotificationPanelComponent: any = null;

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

		const [
			sidebar,
			mobileTabBar,
			globalChatPanel,
			chatSheet,
			keyboardModal,
			notificationPanel,
			historyModal,
			builtinFields,
			textInput,
			selectField,
			checkboxField,
			numberInput,
			mediaLoaderField,
			modelField,
			promptModelField,
			userPicker,
			pluginModal
		] = await Promise.all([
			import('$lib/components/Sidebar.svelte'),
			import('$lib/components/MobileTabBar.svelte'),
			import('$lib/components/GlobalChatPanel.svelte'),
			import('$lib/components/ChatSheet.svelte'),
			import('$lib/components/KeyboardShortcutsModal.svelte'),
			import('$lib/components/notifications/NotificationPanel.svelte'),
			import('$lib/components/modals/GenerationHistoryModal.svelte'),
			import('$lib/fields/builtin'),
			import('$lib/components/form-fields/TextInput.svelte'),
			import('$lib/components/form-fields/SelectField.svelte'),
			import('$lib/components/form-fields/CheckboxField.svelte'),
			import('$lib/components/form-fields/NumberInput.svelte'),
			import('$lib/components/form-fields/MediaLoaderField.svelte'),
			import('$lib/components/form-fields/ModelField.svelte'),
			import('./prompts/components/PromptModelField.svelte'),
			import('$lib/components/ui/UserPicker.svelte'),
			import('$lib/components/modals/PluginModal.svelte')
		]);

		SidebarComponent = sidebar.default;
		MobileTabBarComponent = mobileTabBar.default;
		GlobalChatPanelComponent = globalChatPanel.default;
		ChatSheetComponent = chatSheet.default;
		KeyboardShortcutsModalComponent = keyboardModal.default;
		NotificationPanelComponent = notificationPanel.default;

		registerComponent('GenerationHistoryModal', historyModal.default);
		registerComponent('TextInput', textInput.default);
		registerComponent('SelectField', selectField.default);
		registerComponent('CheckboxField', checkboxField.default);
		registerComponent('NumberInput', numberInput.default);
		registerComponent('MediaLoaderField', mediaLoaderField.default);
		registerComponent('ModelField', modelField.default);
		registerComponent('PromptModelField', promptModelField.default);
		registerComponent('UserPicker', userPicker.default);
		registerComponent('PluginModal', pluginModal.default);
		initHostApi();
		builtinFields.registerBuiltinFieldComponents();
		await refreshPluginExtensions();
		authenticatedRuntimeReady = true;
	}

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
		notifications.load();
		notificationsWebSocket.connect();
		installLimitsWatcher();
		refreshLimits();
	}
	$: if (mounted && !$authStore.isAuthenticated && notificationsInitialized) {
		notificationsInitialized = false;
		notificationsWebSocket.disconnect();
		notifications.reset();
		resetLimitsState();
	}

	let tabDraftSyncStarted = false;
	$: if (mounted && $authStore.isAuthenticated && !tabDraftSyncStarted) {
		tabDraftSyncStarted = true;
		tabDraftSync.start();
	}
	$: if (mounted && !$authStore.isAuthenticated && tabDraftSyncStarted) {
		tabDraftSyncStarted = false;
		tabDraftSync.stop();
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
		<ToolRunChip />
		<ToastContainer />
		<ModeInstallHost />
		<ConfirmHost />
		<IndexingChoiceModal />
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
