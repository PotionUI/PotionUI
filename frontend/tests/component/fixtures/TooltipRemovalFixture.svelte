<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';

	let { show }: { show: boolean } = $props();
</script>

{#if show}
	<Tooltip text="Hint" delay={0}>
		<button type="button" data-removable>trigger</button>
	</Tooltip>
{/if}
