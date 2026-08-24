<script lang="ts">
	/**
	 * Generic mount point for a plugin's `generation.output` renderer: lazily
	 * resolves `pluginId`/`asset` (a manifest `renderers:` entry) and mounts
	 * the resulting component with the raw WebSocket message as `msg`.
	 */
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';

	export let pluginId: string;
	export let asset: string;
	export let msg: unknown;
</script>

{#await resolvePluginComponent(pluginId, asset) then Component}
	{#if Component}
		<svelte:component this={Component} {msg} />
	{/if}
{/await}
