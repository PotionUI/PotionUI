import { describe, it, expect } from 'vitest';
import {
	pluginDetailTabsFor,
	isPluginDetailTab,
	hasHiddenAdminTabs,
	ADMIN_PLUGIN_TABS_HOOK
} from './pluginDetailTabs';
import type { PluginHook } from '$lib/stores/plugins';

function tabHook(overrides: Partial<PluginHook> = {}): PluginHook {
	return {
		id: 1,
		plugin_id: 'comfyui-backend',
		hook_name: ADMIN_PLUGIN_TABS_HOOK,
		hook_type: 'frontend',
		component_path: 'ImportWorkflowTab.js',
		position: 'import-workflow',
		sort_order: 10,
		label: 'Import workflow',
		...overrides
	};
}

describe('pluginDetailTabsFor', () => {
	it('gives a plugin with no settings and no contributed tabs just Overview', () => {
		const tabs = pluginDetailTabsFor({ id: 'plain-plugin' }, []);
		expect(tabs.map((t) => t.id)).toEqual(['overview']);
	});

	it('adds a Settings tab when the plugin declares a settings schema', () => {
		const tabs = pluginDetailTabsFor(
			{ id: 'plain-plugin', settings_schema: [{ name: 'api_key', type: 'string', label: 'API Key' }] },
			[]
		);
		expect(tabs.map((t) => t.id)).toEqual(['overview', 'settings']);
	});

	it('appends contributed tabs for this plugin, ignoring hooks from other plugins', () => {
		const hooks = [
			tabHook({ id: 1, position: 'import-workflow', label: 'Import workflow', sort_order: 10 }),
			tabHook({ id: 2, position: 'imported-presets', label: 'Imported presets', sort_order: 20 }),
			tabHook({ id: 3, plugin_id: 'other-plugin', position: 'other-tab', label: 'Other' })
		];
		const tabs = pluginDetailTabsFor({ id: 'comfyui-backend' }, hooks);
		expect(tabs.map((t) => t.id)).toEqual(['overview', 'import-workflow', 'imported-presets']);
		expect(tabs[1]).toMatchObject({ label: 'Import workflow', componentPath: 'ImportWorkflowTab.js' });
	});

	it('falls back to the hook position as the label when no label is set', () => {
		const hooks = [tabHook({ label: undefined })];
		const tabs = pluginDetailTabsFor({ id: 'comfyui-backend' }, hooks);
		expect(tabs[1].label).toBe('import-workflow');
	});

	it('skips a contributed hook missing a component_path or position', () => {
		const hooks = [tabHook({ component_path: undefined }), tabHook({ id: 2, position: undefined })];
		const tabs = pluginDetailTabsFor({ id: 'comfyui-backend' }, hooks);
		expect(tabs.map((t) => t.id)).toEqual(['overview']);
	});

	it('hides a tab gated by require_role when the user lacks that role', () => {
		const hooks = [tabHook({ require_role: 'ADMIN' })];
		expect(pluginDetailTabsFor({ id: 'comfyui-backend' }, hooks, 'USER').map((t) => t.id)).toEqual(['overview']);
		expect(pluginDetailTabsFor({ id: 'comfyui-backend' }, hooks, 'ADMIN').map((t) => t.id)).toEqual([
			'overview',
			'import-workflow'
		]);
	});

	it('shows a require_role-less tab to every user', () => {
		const hooks = [tabHook({ require_role: undefined })];
		expect(pluginDetailTabsFor({ id: 'comfyui-backend' }, hooks, undefined).map((t) => t.id)).toEqual([
			'overview',
			'import-workflow'
		]);
	});
});

describe('isPluginDetailTab', () => {
	it('is true for overview and settings when applicable', () => {
		const plugin = { id: 'p', settings_schema: [{ name: 'x', type: 'string' as const, label: 'X' }] };
		expect(isPluginDetailTab(plugin, [], 'overview')).toBe(true);
		expect(isPluginDetailTab(plugin, [], 'settings')).toBe(true);
	});

	it('is false for settings when the plugin has no settings schema', () => {
		expect(isPluginDetailTab({ id: 'p' }, [], 'settings')).toBe(false);
	});

	it('is true for a contributed tab id, false for an unknown one', () => {
		const hooks = [tabHook()];
		expect(isPluginDetailTab({ id: 'comfyui-backend' }, hooks, 'import-workflow')).toBe(true);
		expect(isPluginDetailTab({ id: 'comfyui-backend' }, hooks, 'no-such-tab')).toBe(false);
	});
});

describe('hasHiddenAdminTabs', () => {
	it('is false when the plugin is enabled', () => {
		expect(hasHiddenAdminTabs([{ id: 1, plugin_id: 'p', hook_name: ADMIN_PLUGIN_TABS_HOOK, hook_type: 'frontend', sort_order: 0 }], true)).toBe(
			false
		);
	});

	it('is true when disabled and the plugin has an admin.plugin.tabs hook', () => {
		expect(hasHiddenAdminTabs([{ id: 1, plugin_id: 'p', hook_name: ADMIN_PLUGIN_TABS_HOOK, hook_type: 'frontend', sort_order: 0 }], false)).toBe(
			true
		);
	});

	it('is false when disabled but the plugin has no admin.plugin.tabs hook', () => {
		expect(hasHiddenAdminTabs([{ id: 1, plugin_id: 'p', hook_name: 'workbench.actions', hook_type: 'frontend', sort_order: 0 }], false)).toBe(
			false
		);
	});

	it('is false for undefined hooks', () => {
		expect(hasHiddenAdminTabs(undefined, false)).toBe(false);
	});
});
