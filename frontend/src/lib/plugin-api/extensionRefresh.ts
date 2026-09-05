/**
 * The single entry point that (re)builds every frontend extension a plugin can
 * contribute: renderers (`history.artifact`, `workbench.file`, `model.view`,
 * `generation.output`, `chat.tool`), form field types, extension-slot
 * contributions, pages, quick actions and sidebar widgets.
 *
 * Call it at app init and after any successful mutation of the plugin set - an
 * enable, a disable, a rescan - and after a failed initialization. It replaces
 * the one-shot `stores/extensions.ts` + `stores/fieldTypes.ts` init pair,
 * which registered into ever-growing registries and latched their `initialized`
 * flag before the request, so a plugin toggle left stale catalogs behind and a
 * single transient failure disabled every plugin extension until a page reload.
 *
 * Three properties this relies on:
 *
 * SNAPSHOT. Every catalog is fetched before anything is applied. A refresh that
 * cannot read all of them applies nothing and leaves the previously applied
 * snapshot in place, so a dropped request degrades to "not updated yet" rather
 * than "every plugin surface vanished". Nothing latches, so the next call
 * retries.
 *
 * OWNERSHIP. Each registration is made under its plugin's ownership token and
 * remembered with the disposer that reverses exactly it (see
 * `registries/registry.ts` for the layering rules). Applying a snapshot runs
 * the previous snapshot's disposers first, so removing one plugin can neither
 * drop core's registration for a key it shadowed nor another plugin's live one:
 * the shadowed layer becomes visible again instead.
 *
 * COALESCING. At most one refresh is in flight. Callers that arrive during a
 * run share one follow-up run scheduled after it, so a burst of toggles issues
 * two requests rather than one per toggle, and no caller is answered by a
 * snapshot older than its own request.
 */
import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
import {
	registerWorkbenchFileRenderer,
	unregisterWorkbenchFileRenderer
} from '$lib/registries/workbenchFileRendererRegistry';
import { registerModelView, unregisterModelView } from '$lib/registries/modelViewRegistry';
import { chatToolRendererRegistry } from '$lib/registries/chatToolRendererRegistry';
import {
	registerPluginOutputHandler,
	unregisterPluginOutputHandler
} from '$lib/generation/messages/pluginOutput';
import { registerFieldComponent, unregisterFieldComponent } from '$lib/fields/registry';
import { pluginOwner } from '$lib/registries/registry';
import { setContributions, type SlotContribution } from '$lib/extensions/extensionSlots';
import { setPluginRevisions } from '$lib/plugin-api/componentResolver';
import { parseComponentRef } from '$lib/plugin-api/componentRef';
import {
	frontendHooks,
	pluginPages,
	pluginQuickActions,
	sidebarWidgets,
	type FrontendHooks,
	type PluginHook,
	type PluginPage,
	type PluginQuickAction,
	type SidebarWidget
} from '$lib/stores/plugins';

interface FrontendExtensionRenderer {
	plugin_id: string;
	kind: string;
	key: string;
	component: string;
}

interface FieldTypeManifestEntry {
	type: string;
	component: string;
	source: string;
}

interface ExtensionSnapshot {
	renderers: FrontendExtensionRenderer[];
	contributions: SlotContribution[];
	revisions: Record<string, string>;
	fieldTypes: FieldTypeManifestEntry[];
	pages: PluginPage[];
	quickActions: PluginQuickAction[];
	widgets: SidebarWidget[];
	hooks: FrontendHooks;
}

type Disposer = () => void;

let disposers: Disposer[] = [];
let inFlight: Promise<void> | null = null;
let queued: Promise<void> | null = null;

async function fetchCatalog<T>(path: string, pick: (data: any) => T): Promise<T> {
	const response = await api.getClient().get(path);
	const body = response.data;
	if (!body?.success) throw new Error(body?.message || `Request to ${path} was unsuccessful`);
	return pick(body.data);
}

async function fetchSnapshot(): Promise<ExtensionSnapshot> {
	const [extensions, fieldTypes, pages, quickActions, widgets, hooks] = await Promise.all([
		fetchCatalog('/api/plugins/frontend-extensions', (data) => ({
			renderers: (data?.renderers || []) as FrontendExtensionRenderer[],
			contributions: (data?.contributions || []) as SlotContribution[],
			revisions: (data?.revisions || {}) as Record<string, string>
		})),
		fetchCatalog('/api/fields/types', (data) => (data || []) as FieldTypeManifestEntry[]),
		fetchCatalog('/api/plugins/pages', (data) => (Array.isArray(data) ? data : []) as PluginPage[]),
		fetchCatalog(
			'/api/plugins/quick-actions',
			(data) => (Array.isArray(data) ? data : []) as PluginQuickAction[]
		),
		fetchCatalog(
			'/api/plugins/sidebar-widgets',
			(data) => (Array.isArray(data) ? data : []) as SidebarWidget[]
		),
		fetchCatalog('/api/plugins/hooks/frontend', (data) => {
			const sorted: FrontendHooks = {};
			for (const [hookName, entries] of Object.entries(data || {})) {
				sorted[hookName] = (entries as PluginHook[])
					.slice()
					.sort((a, b) => a.sort_order - b.sort_order);
			}
			return sorted;
		})
	]);

	return { ...extensions, fieldTypes, pages, quickActions, widgets, hooks };
}

function registerRenderer(renderer: FrontendExtensionRenderer): Disposer | null {
	const { plugin_id: pluginId, key, component } = renderer;
	const entry = { pluginId, asset: component };
	const owner = pluginOwner(pluginId);

	switch (renderer.kind) {
		case 'history.artifact':
			artifactRendererRegistry.register(key, entry);
			return () => artifactRendererRegistry.unregister(key, owner);
		case 'workbench.file':
			registerWorkbenchFileRenderer(key, entry);
			return () => unregisterWorkbenchFileRenderer(key, owner);
		case 'model.view':
			registerModelView(pluginId, key, component);
			return () => unregisterModelView(pluginId, key);
		case 'generation.output':
			registerPluginOutputHandler(key, pluginId, component);
			return () => unregisterPluginOutputHandler(key, pluginId);
		case 'chat.tool':
			chatToolRendererRegistry.register(key, entry);
			return () => chatToolRendererRegistry.unregister(key, owner);
		default:
			logger.warn(`Unknown renderer kind "${renderer.kind}" from plugin ${pluginId}`);
			return null;
	}
}

function registerFieldType(entry: FieldTypeManifestEntry): Disposer | null {
	// Core entries are already registered statically by `fields/builtin.ts`,
	// which owns the canonical alias table.
	if (entry.source === 'core') return null;

	const ref = parseComponentRef(entry.component);
	if (!ref) return null;

	registerFieldComponent(entry.type, { pluginId: ref.pluginId, asset: ref.asset });
	return () => unregisterFieldComponent(entry.type, pluginOwner(ref.pluginId));
}

function applySnapshot(snapshot: ExtensionSnapshot): void {
	for (const dispose of disposers.reverse()) {
		try {
			dispose();
		} catch (err) {
			logger.error('Failed to dispose a plugin extension registration:', err);
		}
	}
	disposers = [];

	setPluginRevisions(snapshot.revisions);

	const next: Disposer[] = [];
	for (const renderer of snapshot.renderers) {
		if (!renderer.key || !renderer.component) continue;
		const disposer = registerRenderer(renderer);
		if (disposer) next.push(disposer);
	}
	for (const entry of snapshot.fieldTypes) {
		if (!entry.component) continue;
		const disposer = registerFieldType(entry);
		if (disposer) next.push(disposer);
	}
	disposers = next;

	setContributions(snapshot.contributions);
	pluginPages.set(snapshot.pages);
	pluginQuickActions.set(snapshot.quickActions);
	sidebarWidgets.set(snapshot.widgets);
	frontendHooks.set(snapshot.hooks);
}

async function runRefresh(): Promise<void> {
	try {
		applySnapshot(await fetchSnapshot());
	} catch (err) {
		// Non-fatal: core renderers, fields and slots work without plugin
		// extensions, and the previously applied snapshot stays live.
		logger.error('Failed to refresh plugin frontend extensions:', err);
	}
}

/**
 * Rebuild every plugin-contributed frontend extension from the backend's
 * current view. Never throws.
 */
export function refreshPluginExtensions(): Promise<void> {
	if (!inFlight) {
		inFlight = runRefresh().finally(() => {
			inFlight = null;
		});
		return inFlight;
	}

	if (!queued) {
		queued = inFlight.then(() => {
			queued = null;
			return refreshPluginExtensions();
		});
	}
	return queued;
}
