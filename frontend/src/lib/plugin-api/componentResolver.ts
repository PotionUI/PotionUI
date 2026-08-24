/**
 * Shared lazy-loader for plugin-hosted Svelte components compiled to ES
 * modules under a plugin's `frontend/dist/`. Extracted from the
 * `PluginSlot.svelte` Svelte-component branch (component_path ending in
 * `.svelte`/`.js`/`.mjs`) so `PluginSlot`, the plugin page host, and the A4
 * field-component registry all share one import + cache path instead of each
 * re-implementing the dynamic `import()` dance.
 */
import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';

const moduleCache = new Map<string, Promise<any>>();

/**
 * Resolve `pluginId`'s compiled asset (e.g. `ExampleStarsField.js`) to its
 * default-exported component class. `assetPath` may be given with a
 * `.svelte` extension (the manifest/hook source form) - it is normalized to
 * `.js` to match the compiled dist output. Returns `null` on any failure
 * (network, missing default export, etc.) instead of throwing, matching the
 * original `PluginSlot` behavior. Results (including failures) are cached
 * per plugin+asset for the lifetime of the page.
 */
export function resolvePluginComponent(pluginId: string, assetPath: string): Promise<any | null> {
	const componentPath = assetPath.replace(/\.svelte$/, '.js');
	const cacheKey = `${pluginId}:${componentPath}`;

	let cached = moduleCache.get(cacheKey);
	if (!cached) {
		cached = loadPluginComponent(pluginId, componentPath);
		moduleCache.set(cacheKey, cached);
	}
	return cached;
}

async function loadPluginComponent(pluginId: string, componentPath: string): Promise<any | null> {
	const moduleUrl = `${api.getBaseURL()}/api/plugins/${pluginId}/assets/${componentPath}`;

	try {
		const module = await import(/* @vite-ignore */ moduleUrl);

		if (!module.default) {
			logger.error(`[componentResolver] No default export found in ${moduleUrl}`);
			return null;
		}

		return module.default;
	} catch (e) {
		logger.error(`[componentResolver] Failed to import component module ${moduleUrl}:`, e);
		return null;
	}
}

/** Clears the module cache. Test-only escape hatch. */
export function _clearComponentCache(): void {
	moduleCache.clear();
}
