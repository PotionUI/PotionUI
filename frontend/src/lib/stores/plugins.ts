import { logger, getErrorMessage } from '$lib/utils/logger';
import { writable, derived, get } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { api } from '$lib/services/api/index';

// Plugin page interface
export interface PluginPage {
	plugin_id: string;
	route: string;
	component_path: string;
	label: string;
	icon_svg?: string;
	sidebar_order: number;
	show_in_sidebar: boolean;
	require_role?: string;
}

// Types
export interface PluginSettingSchema {
	name: string;
	type: 'string' | 'number' | 'boolean';
	label: string;
	description?: string;
	required?: boolean;
	default?: unknown;
	is_secret?: boolean;
}

export interface Plugin {
	id: string;
	name: string;
	version: string;
	type: 'frontend-only' | 'backend-only' | 'full-stack';
	description?: string;
	author?: string;
	enabled: boolean;
	manifest_path: string;
	installed_at?: string;
	updated_at?: string;
	hooks?: PluginHook[];
	settings_schema?: PluginSettingSchema[];
	settings_values?: Record<string, unknown>;
	/** Runtime lifecycle state from the registry, e.g. "error", "enabled", "disabled" */
	state?: string;
	/** Error message when `state` is "error" (e.g. invalid manifest) */
	error?: string;
	/** Catalogue category id, defaults to "other" when absent. */
	category?: 'generation' | 'models' | 'system' | 'media' | 'workflow' | 'developer' | 'other';
	tags?: string[];
	capabilities?: string[];
	source?: 'marketplace' | 'local';
	homepage?: string;
	repository?: string;
	hook_count?: number;
	settings_count?: number;
}

export interface PluginSetting {
	id: number;
	plugin_id: string;
	setting_key: string;
	setting_value: unknown;
	user_id?: string;
	is_secret: boolean;
}

export interface PluginHook {
	id: number;
	plugin_id: string;
	hook_name: string;
	hook_type: 'backend' | 'frontend';
	handler_path?: string;
	component_path?: string;
	position?: string;
	sort_order: number;
}

export interface FrontendHooks {
	[hookName: string]: PluginHook[];
}

export interface PluginQuickAction {
	plugin_id: string;
	plugin_name: string;
	action_id: string;
	label: string;
	icon?: string;
	endpoint: string;
	method: string;
	confirm?: string;
	require_role?: string;
}

export interface SidebarWidget {
	plugin_id: string;
	widget_id: string;
	position: string;
	component: string;
	order: number;
	label: string;
}

// Individual stores for direct subscription
export const plugins: Writable<Plugin[]> = writable([]);
export const frontendHooks: Writable<FrontendHooks> = writable({});
export const pluginPages: Writable<PluginPage[]> = writable([]);
export const pluginQuickActions: Writable<PluginQuickAction[]> = writable([]);
export const sidebarWidgets: Writable<SidebarWidget[]> = writable([]);
// Whole-catalogue operations only (initial fetch, scan). A per-plugin mutation
// (toggle, settings save) must never set this - it gates the entire plugin
// list's visibility in PluginsTab, so doing so would blank every row for the
// duration of a single row's own request. See `pendingPluginIds` for that.
export const loading: Writable<boolean> = writable(false);
export const error: Writable<string | null> = writable(null);

// Plugin ids with an in-flight per-item mutation (toggle enable/disable,
// settings save). A row-scoped busy indicator reads this instead of `loading`.
export const pendingPluginIds: Writable<Set<string>> = writable(new Set());

function beginPending(pluginId: string): void {
	pendingPluginIds.update((ids) => new Set(ids).add(pluginId));
}

function endPending(pluginId: string): void {
	pendingPluginIds.update((ids) => {
		if (!ids.has(pluginId)) return ids;
		const next = new Set(ids);
		next.delete(pluginId);
		return next;
	});
}

// Store implementation with methods
function createPluginStore() {
	return {
		// Expose stores for backward compatibility
		plugins,
		frontendHooks,
		pluginPages,
		pluginQuickActions,
		sidebarWidgets,
		loading,
		error,
		pendingPluginIds,

		// Load all plugins from API
		async loadPlugins(): Promise<void> {
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().get('/api/plugins');
				const data = response.data;

				if (data.success) {
					plugins.set(Array.isArray(data.data) ? data.data : []);
				} else {
					throw new Error(data.message || 'Failed to load plugins');
				}
			} catch (err: unknown) {
				const errorMessage = getErrorMessage(err, 'Failed to load plugins')
				error.set(errorMessage);
				logger.error('Failed to load plugins:', err);
			} finally {
				loading.set(false);
			}
		},

		// Load frontend hooks from API
		async loadFrontendHooks(): Promise<void> {
			try {
				const response = await api.getClient().get('/api/plugins/hooks/frontend');
				const data = response.data;

				if (data.success) {
					const hooksData = data.data || {};
					const sortedHooks: FrontendHooks = {};
					for (const [hookName, hooks] of Object.entries(hooksData)) {
						sortedHooks[hookName] = (hooks as PluginHook[]).sort(
							(a, b) => a.sort_order - b.sort_order
						);
					}
					frontendHooks.set(sortedHooks);
				} else {
					throw new Error(data.message || 'Failed to load frontend hooks');
				}
			} catch (err: unknown) {
				const errorMessage = getErrorMessage(err, 'Failed to load frontend hooks')
				error.set(errorMessage);
				logger.error('Failed to load frontend hooks:', err);
			}
		},

		// Load plugin pages from API
		async loadPluginPages(): Promise<void> {
			try {
				const response = await api.getClient().get('/api/plugins/pages');
				const data = response.data;

				if (data.success) {
					pluginPages.set(Array.isArray(data.data) ? data.data : []);
				}
			} catch (err: unknown) {
				logger.error('Failed to load plugin pages:', err);
			}
		},

		// Load quick actions from API
		async loadPluginQuickActions(): Promise<void> {
			try {
				const response = await api.getClient().get('/api/plugins/quick-actions');
				const data = response.data;

				if (data.success) {
					pluginQuickActions.set(Array.isArray(data.data) ? data.data : []);
				}
			} catch (err: unknown) {
				logger.error('Failed to load plugin quick actions:', err);
			}
		},

		// Load sidebar widgets from API
		async loadSidebarWidgets(): Promise<void> {
			try {
				const response = await api.getClient().get('/api/plugins/sidebar-widgets');
				const data = response.data;

				if (data.success) {
					sidebarWidgets.set(Array.isArray(data.data) ? data.data : []);
				}
			} catch (err: unknown) {
				logger.error('Failed to load sidebar widgets:', err);
			}
		},

		// Get plugins by hook name
		getPluginsByHook(hookName: string): PluginHook[] {
			const hooks = get(frontendHooks);
			return hooks[hookName] || [];
		},

		// Enable/disable plugin
		async togglePlugin(pluginId: string, enabled: boolean): Promise<boolean> {
			beginPending(pluginId);
			error.set(null);

			try {
				const endpoint = enabled ? 'enable' : 'disable';
				const response = await api.getClient().post(`/api/plugins/${pluginId}/${endpoint}`);
				const data = response.data;

				if (data.success) {
					plugins.update((currentPlugins) =>
						currentPlugins.map((p) => (p.id === pluginId ? { ...p, enabled } : p))
					);

					const currentPlugins = get(plugins);
					const plugin = currentPlugins.find((p) => p.id === pluginId);
					if (plugin && (plugin.type === 'frontend-only' || plugin.type === 'full-stack')) {
						await this.loadFrontendHooks();
					}

					return true;
				} else {
					throw new Error(data.message || `Failed to ${endpoint} plugin`);
				}
			} catch (err: unknown) {
				const errorMessage = getErrorMessage(err, 'Failed to toggle plugin');
				error.set(errorMessage);
				logger.error(`Failed to toggle plugin:`, err);
				return false;
			} finally {
				endPending(pluginId);
			}
		},

		// Get plugin settings
		async getPluginSettings(pluginId: string, userId?: string): Promise<PluginSetting[]> {
			try {
				const params = new URLSearchParams();
				if (userId) {
					params.append('user_id', userId);
				}

				const queryString = params.toString();
				const url = `/api/plugins/${pluginId}/settings${queryString ? `?${queryString}` : ''}`;

				const response = await api.getClient().get(url);
				const data = response.data;

				if (data.success) {
					return Array.isArray(data.data) ? data.data : [];
				} else {
					throw new Error(data.message || 'Failed to load plugin settings');
				}
			} catch (err: unknown) {
				const errorMessage = getErrorMessage(err, 'Failed to load plugin settings')
				error.set(errorMessage);
				logger.error('Failed to load plugin settings:', err);
				return [];
			}
		},

		// Update plugin settings
		async updatePluginSettings(
			pluginId: string,
			settings: Record<string, any>,
			userId?: string
		): Promise<boolean> {
			beginPending(pluginId);
			error.set(null);

			try {
				const params = new URLSearchParams();
				if (userId) {
					params.append('user_id', userId);
				}

				const queryString = params.toString();
				const url = `/api/plugins/${pluginId}/settings${queryString ? `?${queryString}` : ''}`;

				const response = await api.getClient().put(url, { settings });
				const data = response.data;

				if (data.success) {
					return true;
				} else {
					throw new Error(data.message || 'Failed to update plugin settings');
				}
			} catch (err: unknown) {
				const errorMessage = getErrorMessage(err, 'Failed to update plugin settings')
				error.set(errorMessage);
				logger.error('Failed to update plugin settings:', err);
				return false;
			} finally {
				endPending(pluginId);
			}
		},

		// Get plugin details
		async getPluginDetails(pluginId: string): Promise<Plugin | null> {
			try {
				const response = await api.getClient().get(`/api/plugins/${pluginId}`);
				const data = response.data;

				if (data.success && data.data) {
					return data.data as Plugin;
				} else {
					throw new Error(data.message || 'Failed to load plugin details');
				}
			} catch (err: unknown) {
				const errorMessage = getErrorMessage(err, 'Failed to load plugin details')
				error.set(errorMessage);
				logger.error('Failed to load plugin details:', err);
				return null;
			}
		},

		// Scan for new plugins
		async scanPlugins(): Promise<{ newPlugins: number; updatedPlugins: number } | null> {
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().post('/api/plugins/scan');
				const data = response.data;

				if (data.success) {
					await this.loadPlugins();

					return {
						newPlugins: data.data?.new_plugins?.length || 0,
						updatedPlugins: data.data?.updated_plugins?.length || 0
					};
				} else {
					throw new Error(data.message || 'Failed to scan plugins');
				}
			} catch (err: unknown) {
				const errorMessage = getErrorMessage(err, 'Failed to scan plugins')
				error.set(errorMessage);
				logger.error('Failed to scan plugins:', err);
				return null;
			} finally {
				loading.set(false);
			}
		},

		// Initialize store (load plugins, hooks, pages, quick actions, and widgets)
		async initialize(): Promise<void> {
			await this.loadPlugins();
			await this.loadFrontendHooks();
			await this.loadPluginPages();
			await this.loadPluginQuickActions();
			await this.loadSidebarWidgets();
		},

		// Clear error
		clearError(): void {
			error.set(null);
		},

		// Reset store
		reset(): void {
			plugins.set([]);
			frontendHooks.set({});
			pluginPages.set([]);
			pluginQuickActions.set([]);
			sidebarWidgets.set([]);
			loading.set(false);
			error.set(null);
			pendingPluginIds.set(new Set());
		}
	};
}

export const pluginStore = createPluginStore();

export const frontendPlugins = derived(plugins, ($plugins) =>
	$plugins.filter((p) => p.type === 'frontend-only' || p.type === 'full-stack')
);

export const backendPlugins = derived(plugins, ($plugins) =>
	$plugins.filter((p) => p.type === 'backend-only' || p.type === 'full-stack')
);

export const pluginNavItems = derived(pluginPages, ($pages) =>
	($pages || [])
		.filter((p) => p.show_in_sidebar)
		.sort((a, b) => a.sidebar_order - b.sidebar_order)
);
