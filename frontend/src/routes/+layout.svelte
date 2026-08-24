<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto, afterNavigate } from '$app/navigation';
	import { browser } from '$app/environment';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth';
	import { keybindingsStore } from '$lib/stores/keybindings';
	import { chatPanelStore } from '$lib/stores/chatPanel';
	import { init as initKeyboard, destroy as destroyKeyboard } from '$lib/services/keyboard';
	import { registerComponent } from '$lib/plugin-api/componentRegistry';
	import { initHostApi } from '$lib/plugin-api/host';
	import { initFieldTypes } from '$lib/stores/fieldTypes';
	import { initExtensions } from '$lib/stores/extensions';
	import { canInstall, initPwaInstall, promptInstall } from '$lib/stores/pwaInstall';
	import ToastContainer from '$lib/components/ToastContainer.svelte';
	import ConfirmHost from '$lib/components/modals/ConfirmHost.svelte';
	import { notifications } from '$lib/stores/notifications';
	import { notificationsWebSocket } from '$lib/services/notificationsWebsocket';
	import { themeStore } from '$lib/stores/theme';
	import { printStartupBanner } from '$lib/utils/startupBanner';
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
	let KeyboardShortcutsModalComponent: any = null;
	let NotificationPanelComponent: any = null;

	onMount(() => {
		mounted = true;
		printStartupBanner();
		themeStore.init();
		initPwaInstall();
	});

	async function initializeAuthenticatedRuntime() {
		if (authenticatedRuntimeInitialized) return;
		authenticatedRuntimeInitialized = true;

		const [
			sidebar,
			mobileTabBar,
			globalChatPanel,
			keyboardModal,
			notificationPanel,
			historyModal,
			builtinFields,
			textInput,
			selectField,
			checkboxField,
			numberInput,
			mediaLoaderField,
			modelField
		] = await Promise.all([
			import('$lib/components/Sidebar.svelte'),
			import('$lib/components/MobileTabBar.svelte'),
			import('$lib/components/GlobalChatPanel.svelte'),
			import('$lib/components/KeyboardShortcutsModal.svelte'),
			import('$lib/components/notifications/NotificationPanel.svelte'),
			import('$lib/components/modals/GenerationHistoryModal.svelte'),
			import('$lib/fields/builtin'),
			import('$lib/components/form-fields/TextInput.svelte'),
			import('$lib/components/form-fields/SelectField.svelte'),
			import('$lib/components/form-fields/CheckboxField.svelte'),
			import('$lib/components/form-fields/NumberInput.svelte'),
			import('$lib/components/form-fields/MediaLoaderField.svelte'),
			import('$lib/components/form-fields/ModelField.svelte')
		]);

		SidebarComponent = sidebar.default;
		MobileTabBarComponent = mobileTabBar.default;
		GlobalChatPanelComponent = globalChatPanel.default;
		KeyboardShortcutsModalComponent = keyboardModal.default;
		NotificationPanelComponent = notificationPanel.default;

		registerComponent('GenerationHistoryModal', historyModal.default);
		registerComponent('TextInput', textInput.default);
		registerComponent('SelectField', selectField.default);
		registerComponent('CheckboxField', checkboxField.default);
		registerComponent('NumberInput', numberInput.default);
		registerComponent('MediaLoaderField', mediaLoaderField.default);
		registerComponent('ModelField', modelField.default);
		initHostApi();
		builtinFields.registerBuiltinFieldComponents();
		await Promise.all([initFieldTypes(), initExtensions()]);
		authenticatedRuntimeReady = true;
	}

	$: if (mounted && $authStore.isAuthenticated) {
		initializeAuthenticatedRuntime();
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
	}
	$: if (mounted && !$authStore.isAuthenticated && notificationsInitialized) {
		notificationsInitialized = false;
		notificationsWebSocket.disconnect();
		notifications.reset();
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
	$: shouldShowNav = mounted && $authStore.isAuthenticated && !isPublicRoute;

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

	// Redirect to login if not authenticated and not on public route
	$: if (mounted && !$authStore.loading && !$authStore.isAuthenticated && !isPublicRoute) {
		goto('/login');
	}

	// Redirect to generate if authenticated and on public auth page
	$: if (mounted && !$authStore.loading && $authStore.isAuthenticated && isPublicRoute) {
		goto('/generate');
	}
</script>

{#if $authStore.loading}
	<div class="min-h-screen flex items-center justify-center bg-canvas">
		<div class="text-center">
			<div class="animate-spin rounded-full h-12 w-12 border-b-2 border-accent mx-auto mb-4"></div>
			<p class="text-fg-muted">Loading...</p>
		</div>
	</div>
{:else if shouldShowNav}
	<!-- Authenticated layout with sidebar -->
	<div class="min-h-screen bg-canvas">
			<div class="hidden md:block">
				{#if SidebarComponent}<svelte:component this={SidebarComponent} />{/if}
			</div>
		<main class="md:ml-14 min-h-screen overflow-auto pb-16 md:pb-0">
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
			<div class="fixed bottom-16 left-0 right-0 z-50 px-3 pb-2 md:hidden">
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
					<button
						on:click={() => installDismissed = true}
						class="p-1 text-fg-subtle hover:text-fg-muted flex-shrink-0"
						aria-label="Dismiss install prompt"
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
						</svg>
					</button>
				</div>
			</div>
		{/if}
		<div class="hidden md:block">
			{#if GlobalChatPanelComponent}<svelte:component this={GlobalChatPanelComponent} />{/if}
		</div>
		{#if KeyboardShortcutsModalComponent}<svelte:component this={KeyboardShortcutsModalComponent} />{/if}
		{#if NotificationPanelComponent}<svelte:component this={NotificationPanelComponent} />{/if}
		<ToastContainer />
		<ConfirmHost />
	</div>
{:else}
	<!-- Public layout without nav (login page) -->
	<div class="min-h-screen bg-canvas">
		<slot />
		<ToastContainer />
		<ConfirmHost />
	</div>
{/if}
