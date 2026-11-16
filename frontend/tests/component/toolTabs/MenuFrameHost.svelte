<script lang="ts">
	import EditorFrame from '$lib/components/tool-tabs/frame/EditorFrame.svelte';
	import EditorToolbar from '$lib/components/tool-tabs/frame/EditorToolbar.svelte';
	import EditorCanvas from '$lib/components/tool-tabs/frame/EditorCanvas.svelte';
	import OptionsPanel from '$lib/components/tool-tabs/frame/OptionsPanel.svelte';
	import ToolRail from '$lib/components/tool-tabs/frame/ToolRail.svelte';
	import type { EditorMenuContext } from '$lib/editors/menuItems';

	let {
		menu,
		title = 'Crop',
		statusSize = '1536×1024',
		statusExtra = 'Output 1200×800',
		log
	}: { menu: EditorMenuContext; title?: string; statusSize?: string | null; statusExtra?: string | null; log: (entry: string) => void } = $props();
</script>

<EditorFrame canCompare editCount={2} onundo={() => log('undo')} onredo={() => log('redo')} onsave={() => log('save')}>
	{#snippet toolbar()}
		<EditorToolbar
			fileName="harbor-dawn.png"
			{title}
			titleIcon="editor-crop"
			{menu}
			canUndo
			replace={{ allowed: false }}
			onundo={() => log('undo')}
			onredo={() => log('redo')}
			onsave={() => undefined}
		/>
	{/snippet}
	{#snippet rail()}
		<ToolRail groups={menu.rail} active={menu.tool} variant="icons" onselect={(id) => log(`rail:${id}`)} />
	{/snippet}
	{#snippet canvas()}
		<EditorCanvas mediaWidth={100} mediaHeight={100} {statusSize} {statusExtra}>
			{#snippet layer()}<div data-host-layer></div>{/snippet}
		</EditorCanvas>
	{/snippet}
	{#snippet options()}
		<OptionsPanel title={title} icon="editor-crop" onapply={() => log('apply')} onreset={() => log('reset')}>
			<p data-host-option>Options</p>
		</OptionsPanel>
	{/snippet}
</EditorFrame>
