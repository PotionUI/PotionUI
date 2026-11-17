<script lang="ts">
	import ToolRail from '$lib/components/tool-tabs/frame/ToolRail.svelte';
	import EditorFrame from '$lib/components/tool-tabs/frame/EditorFrame.svelte';
	import EditorCanvas from '$lib/components/tool-tabs/frame/EditorCanvas.svelte';

	let {
		phone = false,
		active = true,
		canCompare = true,
		withApply = true,
		compare = $bindable(false),
		panelSide,
		selected = 'crop',
		withDock = false,
		log
	}: {
		phone?: boolean;
		active?: boolean;
		canCompare?: boolean;
		withApply?: boolean;
		compare?: boolean;
		panelSide?: 'left' | 'right';
		selected?: string;
		withDock?: boolean;
		log: (entry: string) => void;
	} = $props();

	const groups = [
		{
			id: 'main',
			label: 'Main',
			tools: [
				{ id: 'crop', label: 'Crop', icon: 'editor-layers' },
				{ id: 'rotate', label: 'Rotate', icon: 'editor-compare' }
			]
		}
	];
</script>

{#snippet dockContent()}<div data-host-dock>dock</div>{/snippet}

<EditorFrame
	{phone}
	{panelSide}
	dock={withDock ? dockContent : undefined}
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
	{#snippet rail()}
		<div data-host-rail class="contents">
			<ToolRail {groups} active={selected} variant={phone ? 'strip' : 'icons'} onselect={(id) => log(`tool:${id}`)} />
		</div>
	{/snippet}
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
