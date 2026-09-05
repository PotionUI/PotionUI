// @vitest-environment jsdom
//
// A plugin's `admin_tabs[]` manifest entries reach the frontend as
// `admin.plugin.tabs` hooks (see src/features/plugins/operations/scan.py and
// frontend/src/routes/admin/components/pluginDetailTabs.ts). This mounts the
// real PluginsTab detail panel against a fake hook entry and proves: the tab
// strip renders it, selecting it resolves and mounts the plugin's own
// component through `resolvePluginComponent` with the promised props, and a
// disabled plugin (whose hook the backend never surfaces in $frontendHooks)
// shows the "enable to see its tabs" hint instead.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';

let StubComponent: any;

const ADMIN_TAB_HOOK = {
	id: 1,
	plugin_id: 'comfyui-backend',
	hook_name: 'admin.plugin.tabs',
	hook_type: 'frontend',
	handler_path: null,
	component_path: 'ImportWorkflowTab.js',
	position: 'import-workflow',
	sort_order: 10,
	label: 'Import workflow',
	require_role: null
};

const PLUGIN_LIST_ENTRY = {
	id: 'comfyui-backend',
	name: 'ComfyUI Backend',
	version: '1.0.0',
	type: 'full-stack',
	enabled: true,
	manifest_path: '/plugins/comfyui-backend/manifest.yml',
	description: 'ComfyUI backend integration',
	category: 'workflow',
	tags: [],
	capabilities: [],
	source: 'marketplace'
};

const PLUGIN_DETAIL = {
	...PLUGIN_LIST_ENTRY,
	hooks: [
		{
			id: 1,
			plugin_id: 'comfyui-backend',
			hook_name: 'admin.plugin.tabs',
			hook_type: 'frontend',
			component_path: 'ImportWorkflowTab.js',
			position: 'import-workflow',
			sort_order: 10,
			label: 'Import workflow'
		}
	],
	settings_schema: [],
	settings_values: {}
};

// Mutable fixture the mocked API reads from - reset in afterEach.
const apiState: { listEntry: typeof PLUGIN_LIST_ENTRY; adminTabHooks: (typeof ADMIN_TAB_HOOK)[]; detail: typeof PLUGIN_DETAIL } = {
	listEntry: PLUGIN_LIST_ENTRY,
	adminTabHooks: [ADMIN_TAB_HOOK],
	detail: PLUGIN_DETAIL
};

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
	return {
		...actual,
		api: {
			...actual.api,
			getClient: () => ({
				get: vi.fn(async (url: string) => {
					if (url === '/api/plugins') return { data: { success: true, data: [apiState.listEntry] } };
					if (url === '/api/plugins/hooks/frontend') {
						return {
							data: {
								success: true,
								data: apiState.adminTabHooks.length ? { 'admin.plugin.tabs': apiState.adminTabHooks } : {}
							}
						};
					}
					if (url === `/api/plugins/${apiState.listEntry.id}`) return { data: { success: true, data: apiState.detail } };
					// The rest of the catalogues one refreshPluginExtensions() snapshot reads.
					if (url === '/api/plugins/frontend-extensions') {
						return { data: { success: true, data: { renderers: [], contributions: [], revisions: {} } } };
					}
					if (url === '/api/fields/types') return { data: { success: true, data: [] } };
					if (url === '/api/plugins/pages') return { data: { success: true, data: [] } };
					if (url === '/api/plugins/quick-actions') return { data: { success: true, data: [] } };
					if (url === '/api/plugins/sidebar-widgets') return { data: { success: true, data: [] } };
					throw new Error(`unexpected GET ${url}`);
				}),
				post: vi.fn(async () => ({ data: { success: true } }))
			})
		}
	};
});

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn(async () => StubComponent),
	setPluginRevisions: vi.fn()
}));

const { default: PluginsTab } = await import('../../src/routes/admin/components/PluginsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');
StubComponent = (await import('./stubs/StubPluginTab.svelte')).default;

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: PluginsTab as never, target, props: {} });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function clickPluginRow(target: HTMLElement, name: string) {
	const row = Array.from(target.querySelectorAll('button, [role="option"]')).find((el) =>
		el.textContent?.includes(name)
	) as HTMLElement | undefined;
	expect(row).toBeTruthy();
	flushSync(() => row!.click());
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	apiState.listEntry = PLUGIN_LIST_ENTRY;
	apiState.adminTabHooks = [ADMIN_TAB_HOOK];
	apiState.detail = PLUGIN_DETAIL;
});

describe('PluginsTab admin.plugin.tabs contributed tab', () => {
	it('renders the contributed tab and mounts the plugin component through the resolver on select', async () => {
		mounted = mount();
		await settle();
		expect(mounted.target.textContent).toContain('ComfyUI Backend');

		clickPluginRow(mounted.target, 'ComfyUI Backend');
		await settle();

		const tabNav = mounted.target.querySelector('nav[aria-label="Plugin details"]');
		expect(tabNav).toBeTruthy();
		expect(tabNav!.textContent).toContain('Overview');
		expect(tabNav!.textContent).toContain('Import workflow');
		expect(tabNav!.textContent).not.toContain('Settings');

		const contributedTabButton = Array.from(tabNav!.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Import workflow')
		) as HTMLButtonElement;
		expect(contributedTabButton).toBeTruthy();

		flushSync(() => contributedTabButton.click());
		await settle();

		const stub = mounted.target.querySelector('[data-testid="stub-plugin-tab"]');
		expect(stub).toBeTruthy();
		expect(stub!.textContent).toBe('Stub tab for comfyui-backend (ComfyUI Backend)');
	});

	it('hides the contributed tab and shows a hint for a disabled plugin', async () => {
		// The backend never surfaces admin.plugin.tabs in $frontendHooks for a
		// disabled plugin (PluginRepository.get_hooks_by_type joins on
		// plugins.enabled) - only the plugin's own detail payload still carries
		// the hook unconditionally, which is the "hidden" signal the hint reads.
		apiState.listEntry = { ...PLUGIN_LIST_ENTRY, enabled: false };
		apiState.adminTabHooks = [];
		apiState.detail = { ...PLUGIN_DETAIL, enabled: false };

		mounted = mount();
		await settle();

		clickPluginRow(mounted.target, 'ComfyUI Backend');
		await settle();

		const tabNav = mounted.target.querySelector('nav[aria-label="Plugin details"]');
		expect(tabNav!.textContent).not.toContain('Import workflow');
		expect(mounted.target.textContent).toContain('Enable this plugin to see its additional tabs.');
	});
});
