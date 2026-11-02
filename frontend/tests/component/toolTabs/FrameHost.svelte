<script lang="ts">
	import EditorFrame from '$lib/components/tool-tabs/frame/EditorFrame.svelte';
	import EditorCanvas from '$lib/components/tool-tabs/frame/EditorCanvas.svelte';

	let {
		phone = false,
		active = true,
		canCompare = true,
		withApply = true,
		compare = $bindable(false),
		log
	}: {
		phone?: boolean;
		active?: boolean;
		canCompare?: boolean;
		withApply?: boolean;
		compare?: boolean;
		log: (entry: string) => void;
	} = $props();
</script>

<EditorFrame
	{phone}
	{active}
	{canCompare}
	bind:compare
	editCount={2}
	onundo={() => log('undo')}
	onredo={() => log('redo')}
	onsave={() => log('save')}
	onapply={withApply ? () => log('apply') : undefined}
>
	{#snippet toolbar()}<div data-host-toolbar>toolbar</div>{/snippet}
	{#snippet rail()}<div data-host-rail>rail</div>{/snippet}
	{#snippet canvas()}
		<EditorCanvas mediaWidth={100} mediaHeight={100} {compare} {phone}>
			{#snippet layer()}<div data-host-layer></div>{/snippet}
		</EditorCanvas>
	{/snippet}
	{#snippet options()}
		<div data-host-options>
			<input data-host-input />
			<button type="button" data-host-button>Pick</button>
			<input type="checkbox" aria-label="Keep pitch" data-host-checkbox />
		</div>
	{/snippet}
	{#snippet stack(variant)}<div data-host-stack={variant}>stack {variant}</div>{/snippet}
</EditorFrame>
