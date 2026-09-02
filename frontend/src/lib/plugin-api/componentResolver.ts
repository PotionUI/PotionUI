/**
 * Shared lazy-loader for plugin-hosted Svelte components compiled to ES
 * modules under a plugin's `frontend/dist/`. Extracted from the
 * `PluginSlot.svelte` Svelte-component branch (component_path ending in
 * `.svelte`/`.js`/`.mjs`) so `PluginSlot`, the plugin page host, and the A4
 * field-component registry all share one import + cache path instead of each
 * re-implementing the dynamic `import()` dance.
 *
 * Every resolved component is wrapped before it's handed back: a plugin dist
 * bundles its OWN copy of the Svelte runtime (`scripts/build-plugins.mjs`
 * compiles it with `compatibility: { componentApi: 4 }`, whose class-API
 * shim self-mounts through that bundled runtime), so mounting it through the
 * host runtime's `<svelte:component>` crashes - the host's enclosing effect
 * belongs to a different runtime instance than the one the dist was compiled
 * against. `PluginDistHost.svelte` bridges the two by mounting the dist
 * imperatively via its class API inside a host-runtime component, so callers
 * can keep using `<svelte:component>` on whatever this module returns.
 */
import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import PluginDistHost from '$lib/components/plugins/PluginDistHost.svelte';

const moduleCache = new Map<string, Promise<any>>();

/** Wraps a raw plugin dist export in a host-mountable Svelte 5 function component. Exported for direct testing (`pluginDistHostMount.test.ts`) - real callers only reach it through `resolvePluginComponent`. */
export function _wrapPluginDistComponent(dist: any): any {
	function PluginComponentHost(anchor: any, props: Record<string, any>) {
		// A prototype trick (`Object.create`) would hide the caller's props from
		// Svelte's rest-props proxy: its `ownKeys` trap is `Reflect.ownKeys`,
		// which is own-properties-only and does not walk the prototype chain -
		// a real `Proxy` is required to both add `component` and keep the
		// caller's own (lazily-getter-backed) prop keys live and enumerable.
		const hostProps = new Proxy(props, {
			get: (target, key) => (key === 'component' ? dist : Reflect.get(target, key)),
			has: (target, key) => key === 'component' || Reflect.has(target, key),
			ownKeys: (target) => [...new Set([...Reflect.ownKeys(target), 'component'])],
			getOwnPropertyDescriptor: (target, key) =>
				key === 'component'
					? { value: dist, enumerable: true, configurable: true }
					: Reflect.getOwnPropertyDescriptor(target, key)
		});
		return PluginDistHost(anchor, hostProps as any);
	}
	PluginComponentHost.dist = dist;
	return PluginComponentHost;
}

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

		return _wrapPluginDistComponent(module.default);
	} catch (e) {
		logger.error(`[componentResolver] Failed to import component module ${moduleUrl}:`, e);
		return null;
	}
}

/** Clears the module cache. Test-only escape hatch. */
export function _clearComponentCache(): void {
	moduleCache.clear();
}
