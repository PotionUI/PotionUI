<script lang="ts">
	import MaskOptions from '$lib/components/tool-tabs/image/MaskOptions.svelte';

	let brush = $state(20);
	let erase = $state(false);
</script>

<MaskOptions bind:brush bind:erase />
<output data-bound>{brush}|{erase}</output>
