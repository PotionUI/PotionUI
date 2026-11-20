<script lang="ts">
	import MaskLayer from '$lib/components/tool-tabs/image/MaskLayer.svelte';
	import MaskOptions from '$lib/components/tool-tabs/image/MaskOptions.svelte';

	let {
		width,
		height,
		onstroke,
		onready
	}: {
		width: number;
		height: number;
		onstroke?: (painted: boolean) => void;
		onready?: (layer: ReturnType<typeof MaskLayer>) => void;
	} = $props();

	let brush = $state(10);
	let erase = $state(false);
	let layer = $state<ReturnType<typeof MaskLayer>>();

	$effect(() => {
		if (layer) onready?.(layer);
	});
</script>

<div class="relative" data-fixture-stage>
	<MaskLayer bind:this={layer} {width} {height} {brush} {erase} {onstroke} />
</div>
<div data-fixture-options>
	<MaskOptions bind:brush bind:erase />
</div>
