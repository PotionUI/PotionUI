<script lang="ts">
	/**
	 * Mounts a plugin dist component (`component`) inside the host component
	 * tree. `component` is a class-API constructor: it was compiled by
	 * `scripts/build-plugins.mjs` with its own bundled Svelte runtime, and a
	 * component can only be mounted by the runtime it was compiled against
	 * (see `plugin-api/componentResolver.ts`), so the host runtime can never
	 * invoke it directly the way `<svelte:component>` invokes a host-compiled
	 * one - it has to go through the dist's own class shim instead.
	 */
	import { untrack } from 'svelte';
	import { logger } from '$lib/utils/logger';

	let { component, ...rest }: { component: any; [key: string]: any } = $props();

	let el: HTMLDivElement;
	let instance: any;

	$effect(() => {
		const Component = component;
		try {
			instance = new Component({ target: el, props: untrack(() => ({ ...rest })) });
		} catch (e) {
			logger.error('[PluginDistHost] Failed to mount plugin component:', e);
			instance = null;
		}
		return () => {
			if (instance && typeof instance.$destroy === 'function') {
				try {
					instance.$destroy();
				} catch (e) {
					logger.error('[PluginDistHost] Error destroying plugin component:', e);
				}
			}
			instance = null;
		};
	});

	$effect(() => {
		const props = { ...rest };
		if (instance && typeof instance.$set === 'function') {
			instance.$set(props);
		}
	});
</script>

<div bind:this={el} style="display: contents"></div>
