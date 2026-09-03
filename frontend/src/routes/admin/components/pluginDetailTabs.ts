import type { PluginHook, PluginSettingSchema } from '$lib/stores/plugins';

/** The frontend hook a plugin's `admin_tabs[]` manifest entries are delivered
 * through - one `PluginHook` row per tab, `position` carrying the tab's own
 * `id` and `label`/`require_role` riding the columns added for this. */
export const ADMIN_PLUGIN_TABS_HOOK = 'admin.plugin.tabs';

export type PluginDetailTabId = string;

export interface PluginDetailTabDescriptor {
	id: PluginDetailTabId;
	label: string;
	icon?: string;
	/** Present only on a plugin-contributed tab - the dist asset to mount via `resolvePluginComponent`. */
	componentPath?: string;
}

/**
 * Detail-pane tabs for a plugin: the two core tabs (Overview always,
 * Settings only when the plugin declares a settings schema) followed by
 * whatever `admin_tabs` the plugin itself contributes.
 *
 * `frontendHooksForTab` is `$frontendHooks[ADMIN_PLUGIN_TABS_HOOK]` - it only
 * ever contains hooks for an ENABLED plugin (`PluginRepository.get_hooks_by_type`
 * joins on `plugins.enabled`), so a disabled plugin's contributed tabs
 * disappear here for free; see `hasHiddenAdminTabs` for the "why" hint.
 */
export function pluginDetailTabsFor(
	plugin: { id: string; settings_schema?: PluginSettingSchema[] },
	frontendHooksForTab: PluginHook[],
	userRole?: string
): PluginDetailTabDescriptor[] {
	const tabs: PluginDetailTabDescriptor[] = [{ id: 'overview', label: 'Overview', icon: 'info' }];

	if ((plugin.settings_schema?.length ?? 0) > 0) {
		tabs.push({ id: 'settings', label: 'Settings', icon: 'sliders' });
	}

	const contributed = frontendHooksForTab
		.filter((h) => h.plugin_id === plugin.id && !!h.component_path && !!h.position)
		.filter((h) => !h.require_role || h.require_role === userRole)
		.map((h) => ({
			id: h.position as string,
			label: h.label || (h.position as string),
			componentPath: h.component_path as string
		}));

	return [...tabs, ...contributed];
}

/** True when `tab` is one of `plugin`'s own detail tabs - used to fall back to
 * Overview when the previously-selected plugin's tab doesn't exist on the
 * newly-selected one (a different settings schema, disabled, no contributed tabs). */
export function isPluginDetailTab(
	plugin: { id: string; settings_schema?: PluginSettingSchema[] },
	frontendHooksForTab: PluginHook[],
	tab: string,
	userRole?: string
): boolean {
	return pluginDetailTabsFor(plugin, frontendHooksForTab, userRole).some((t) => t.id === tab);
}

/** True when a disabled plugin has `admin_tabs` that would show once enabled -
 * drives the "enable this plugin to see its tabs" hint. `allHooksForPlugin` is
 * the plugin's own unconditional hook list (its detail payload's `hooks`),
 * not the enabled-only `$frontendHooks` store. */
export function hasHiddenAdminTabs(allHooksForPlugin: PluginHook[] | undefined, enabled: boolean): boolean {
	if (enabled) return false;
	return (allHooksForPlugin ?? []).some((h) => h.hook_name === ADMIN_PLUGIN_TABS_HOOK);
}
