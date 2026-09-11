// @vitest-environment jsdom
//
// Sidebar used to call refreshPluginExtensions() again in its own onMount,
// duplicating the layout's boot-time refresh (+layout.svelte awaits it
// before the slot renders). Since Sidebar mounts while that first refresh is
// still in flight, the coalescing queue in extensionRefresh.ts (see its own
// tests) scheduled a full second six-catalogue round on every boot. Sidebar
// must render purely from the stores the layout's refresh already fills.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { writable } from 'svelte/store';

vi.mock('$lib/plugin-api/extensionRefresh', () => ({
	refreshPluginExtensions: vi.fn(() => Promise.resolve())
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: writable({ isAuthenticated: true, token: null, user: null, loading: false, error: null })
}));

vi.mock('$lib/stores/keybindings', () => ({
	keybindingsStore: {
		subscribe: writable({ bindings: [], helpPanelOpen: false, loaded: false }).subscribe,
		registerHandler: vi.fn(),
		unregisterHandler: vi.fn(),
		openHelp: vi.fn(),
		toggleHelp: vi.fn()
	},
	shortcutLabels: writable<Record<string, string | undefined>>({})
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }) }),
		getBaseURL: () => '',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		getReadiness: vi.fn().mockResolvedValue({ overall: 'ready' }),
		getKeybindings: vi.fn().mockResolvedValue({ success: true, data: { keybindings: [] } })
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getBackends: vi.fn().mockResolvedValue({ success: true, data: [] }),
	invokeBackendQuickAction: vi.fn()
}));

const { refreshPluginExtensions } = await import('$lib/plugin-api/extensionRefresh');
const { default: Sidebar } = await import('$lib/components/Sidebar.svelte');
const { createClassComponent } = await import('svelte/legacy');

let component: ReturnType<typeof createClassComponent> | null = null;
let target: HTMLDivElement;

async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

afterEach(() => {
	component?.$destroy();
	component = null;
	target?.remove();
	vi.clearAllMocks();
});

describe('Sidebar boot', () => {
	it('never calls refreshPluginExtensions itself - the layout owns the boot-time refresh', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = createClassComponent({ component: Sidebar as never, target, props: {} });
		flushSync();
		await settle();

		expect(refreshPluginExtensions).not.toHaveBeenCalled();
	});
});
