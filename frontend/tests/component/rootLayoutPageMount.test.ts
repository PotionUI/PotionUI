// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { settle } from './helpers/settle';

vi.mock('$lib/stores/auth', async () => {
	const { writable } = await import('svelte/store');
	return {
		authStore: writable({ isAuthenticated: true, token: 't', user: null, loading: false, error: null })
	};
});
vi.mock('$lib/plans/store', () => ({
	installLimitsWatcher: vi.fn(),
	refreshLimits: vi.fn(),
	resetLimitsState: vi.fn()
}));
vi.mock('$lib/stores/keybindings', async () => {
	const { writable: store } = await import('svelte/store');
	return {
		isHelpOpen: store(false),
		keybindingsStore: {
			loadBindings: vi.fn().mockResolvedValue(undefined),
			reset: vi.fn(),
			registerHandler: vi.fn(),
			toggleHelp: vi.fn()
		}
	};
});
vi.mock('$lib/services/keyboard', () => ({ init: vi.fn(), destroy: vi.fn() }));
vi.mock('$lib/plugin-api/host', () => ({ initHostApi: vi.fn(), whenHostReady: vi.fn().mockResolvedValue(undefined) }));
vi.mock('$lib/plugin-api/extensionRefresh', () => ({
	refreshPluginExtensions: vi.fn().mockResolvedValue(undefined),
	prefetchExtensionSnapshot: vi.fn()
}));
vi.mock('$lib/stores/presetsCatalog', () => ({ loadPresets: vi.fn().mockResolvedValue(undefined) }));
vi.mock('$lib/utils/modeLabel.svelte', () => ({ loadModeLabels: vi.fn().mockResolvedValue(undefined) }));
vi.mock('$lib/stores/pwaInstall', async () => {
	const { writable: store } = await import('svelte/store');
	return { canInstall: store(false), initPwaInstall: vi.fn(), promptInstall: vi.fn() };
});
vi.mock('$lib/stores/accounts', () => ({ initCrossTab: () => () => {} }));
vi.mock('$lib/stores/notifications', async () => {
	const { writable: store } = await import('svelte/store');
	const { subscribe } = store({ unreadCount: 0 });
	return { notifications: { subscribe, load: vi.fn(), reset: vi.fn() } };
});
vi.mock('$lib/stores/tabDraftSync', () => ({ tabDraftSync: { start: vi.fn(), stop: vi.fn() } }));
vi.mock('$lib/services/notificationsWebsocket', () => ({
	notificationsWebSocket: { connect: vi.fn(), disconnect: vi.fn() }
}));
vi.mock('$lib/stores/theme', () => ({ themeStore: { init: vi.fn(), destroy: vi.fn() } }));
vi.mock('$lib/utils/startupBanner', () => ({ printStartupBanner: vi.fn() }));
vi.mock('$lib/utils/inputModality', () => ({ installInputModality: () => () => {} }));

const hostLoads = vi.hoisted(() => ({ modeInstall: 0 }));
vi.mock('$lib/components/recipes/modeInstall/ModeInstallHost.svelte', async () => {
	hostLoads.modeInstall += 1;
	return { default: (await import('./stubs/BlankStub.svelte')).default };
});

const stubComponent = vi.hoisted(() => async () => ({ default: (await import('./stubs/BlankStub.svelte')).default }));
vi.mock('$lib/components/library/LibraryModalHost.svelte', stubComponent);
vi.mock('$lib/components/modals/ConfirmHost.svelte', stubComponent);
vi.mock('$lib/models-location/IndexingChoiceModal.svelte', stubComponent);
vi.mock('$lib/components/accounts/AccountSwitchOverlay.svelte', stubComponent);
vi.mock('$lib/components/Sidebar.svelte', stubComponent);
vi.mock('$lib/components/MobileTabBar.svelte', stubComponent);
vi.mock('$lib/components/GlobalChatPanel.svelte', stubComponent);
vi.mock('$lib/components/ChatSheet.svelte', stubComponent);
vi.mock('$lib/components/KeyboardShortcutsModal.svelte', stubComponent);
vi.mock('$lib/components/notifications/NotificationPanel.svelte', stubComponent);
vi.mock('$lib/components/modals/GenerationHistoryModal.svelte', stubComponent);
vi.mock('$lib/components/form-fields/TextInput.svelte', stubComponent);
vi.mock('$lib/components/form-fields/SelectField.svelte', stubComponent);
vi.mock('$lib/components/form-fields/CheckboxField.svelte', stubComponent);
vi.mock('$lib/components/form-fields/NumberInput.svelte', stubComponent);
vi.mock('$lib/components/form-fields/MediaLoaderField.svelte', stubComponent);
vi.mock('$lib/components/form-fields/ModelField.svelte', stubComponent);
vi.mock('$lib/components/ui/UserPicker.svelte', stubComponent);
vi.mock('$lib/components/modals/PluginModal.svelte', stubComponent);
vi.mock('../../src/routes/prompts/components/PromptModelField.svelte', stubComponent);
vi.mock('$lib/fields/builtin', () => ({ registerBuiltinFieldComponents: vi.fn() }));

const { default: LayoutWithProbe } = await import('./stubs/LayoutWithProbe.svelte');
const { page } = await import('./stubs/appStores');
const { openModeInstall, closeModeInstall } = await import('$lib/stores/modeInstall');

let target: HTMLElement | undefined;
let instance: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (instance) unmount(instance);
	target?.remove();
	instance = undefined;
	target = undefined;
});

describe('root layout page mount', () => {
	it('mounts the routed page once when auth is already settled before the layout mounts', async () => {
		page.update((current) => ({ ...current, url: new URL('http://localhost/library') }));
		const onProbeMount = vi.fn();
		target = document.createElement('div');
		document.body.appendChild(target);

		instance = mount(LayoutWithProbe, { target, props: { onProbeMount } });
		flushSync();
		await settle(6);
		flushSync();

		expect(target.querySelector('[data-mount-probe]')).not.toBeNull();
		expect(onProbeMount).toHaveBeenCalledTimes(1);
	});
	it('loads the mode install host only once an install is requested', async () => {
		page.update((current) => ({ ...current, url: new URL('http://localhost/library') }));
		target = document.createElement('div');
		document.body.appendChild(target);

		instance = mount(LayoutWithProbe, { target, props: { onProbeMount: vi.fn() } });
		flushSync();
		await settle(6);
		expect(hostLoads.modeInstall).toBe(0);

		openModeInstall('recipe', ['m']);
		await vi.waitFor(() => expect(hostLoads.modeInstall).toBe(1));
		closeModeInstall();
	});
});
