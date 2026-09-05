<script lang="ts">
	/**
	 * The live-workspace host for plugin-declared `generation.output` renderers.
	 * Mounted next to the workbench, so it inherits that pane's per-tab gating;
	 * the run filter below is what keeps a completed run's output from being
	 * shown as a result of the run that replaced it.
	 */
	import type { Tab } from '$lib/types/tabs';
	import PluginMessageRenderer from './PluginMessageRenderer.svelte';

	export let tab: Tab;

	$: current = tab.generation.currentGeneration;
	$: runId = current?.generation_id ?? current?.id ?? null;

	$: outputs = Object.entries(tab.generation.pluginOutputs || {})
		.filter(([, entry]) => runId !== null && entry.generationId === runId)
		.map(([messageType, entry]) => ({ messageType, ...entry }));
</script>

{#if outputs.length > 0}
	<div class="flex flex-col gap-2" data-plugin-outputs>
		{#each outputs as output (output.messageType)}
			<PluginMessageRenderer
				pluginId={output.pluginId}
				asset={output.asset}
				msg={output.msg}
				messageType={output.messageType}
			/>
		{/each}
	</div>
{/if}
