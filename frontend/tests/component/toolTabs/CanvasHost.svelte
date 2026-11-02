<script lang="ts">
	import EditorCanvas from '$lib/components/tool-tabs/frame/EditorCanvas.svelte';
	import CanvasChip from '$lib/components/tool-tabs/frame/CanvasChip.svelte';
	import { fitViewport, type Viewport } from '$lib/tool-tabs/frame/viewport';

	let {
		compare = false,
		phone = false,
		spaceHeld = false,
		withBadge = true,
		checker = false,
		viewport = $bindable(fitViewport()),
		split = $bindable(0.5)
	}: {
		compare?: boolean;
		phone?: boolean;
		spaceHeld?: boolean;
		withBadge?: boolean;
		checker?: boolean;
		viewport?: Viewport;
		split?: number;
	} = $props();
</script>

<EditorCanvas mediaWidth={1000} mediaHeight={500} {compare} {phone} {spaceHeld} {checker} afterLabel="After · 00:03.12" bind:viewport bind:split>
	{#snippet layer()}<div data-host-after></div>{/snippet}
	{#snippet before()}<div data-host-before></div>{/snippet}
	{#snippet badge()}{#if withBadge}<CanvasChip>Paused · 00:03.12</CanvasChip>{/if}{/snippet}
</EditorCanvas>
<output data-host-state>{JSON.stringify({ viewport, split })}</output>
