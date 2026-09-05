<script lang="ts">
	/**
	 * Generic mount point for a plugin's `generation.output` renderer: lazily
	 * resolves `pluginId`/`asset` (a manifest `renderers:` entry) and mounts
	 * the resulting component with the raw WebSocket message as `msg`.
	 */
	import { onDestroy } from 'svelte';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import Badge from '$lib/components/ui/Badge.svelte';

	export let pluginId: string;
	export let asset: string;
	export let msg: unknown;
	export let messageType: string = '';

	let component: any = null;
	let unavailable = false;

	// A resolve is asynchronous and unordered: without this counter a load
	// started for a plugin since disabled (or for an asset the props have
	// since moved off) resolves last and mounts a component this renderer is
	// no longer showing. Every prop change and the teardown bump it, so only
	// the newest run may publish. Same guard as `PluginSlot`.
	let loadGeneration = 0;

	function invalidateLoads(): number {
		return ++loadGeneration;
	}

	async function load(id: string, path: string) {
		const generation = invalidateLoads();
		component = null;
		unavailable = false;

		const resolved = await resolvePluginComponent(id, path);
		if (generation !== loadGeneration) return;

		component = resolved;
		unavailable = resolved === null;
	}

	$: load(pluginId, asset);

	onDestroy(invalidateLoads);
</script>

{#if component}
	<svelte:component this={component} {msg} />
{:else if unavailable}
	<div
		class="flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2"
		data-plugin-output-unavailable={pluginId}
	>
		<Badge variant="warning" size="sm">Unavailable</Badge>
		<span class="text-xs text-fg-muted">
			{pluginId} could not load its renderer{messageType ? ` for ${messageType}` : ''}.
		</span>
	</div>
{/if}
