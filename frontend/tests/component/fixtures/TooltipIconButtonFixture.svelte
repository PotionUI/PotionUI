<script lang="ts">
	// Minimal stand-in for the generation panel's icon-button pattern
	// (GenerationPanel.svelte: <Tooltip position="top">…<button class="icon-button">…)
	// so the Tooltip/positioning wiring can be verified without mounting the
	// whole panel and its generation-state props.
	import Tooltip from '$lib/components/Tooltip.svelte';
	export let text: string;
</script>

<Tooltip {text} position="top" delay={0}>
	<button type="button" class="icon-button" aria-label={text}>trigger</button>
</Tooltip>
