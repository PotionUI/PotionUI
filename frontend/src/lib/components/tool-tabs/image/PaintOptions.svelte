<script lang="ts">
	import ImageSourceMenu from '$lib/components/imageEditor/ImageSourceMenu.svelte';
	import LayersPanel from '$lib/components/imageEditor/LayersPanel.svelte';
	import ToolButton from '$lib/components/imageEditor/ToolButton.svelte';
	import ToolPanel from '$lib/components/imageEditor/ToolPanel.svelte';
	import type { PaintSession, SessionSnapshot } from '$lib/components/imageEditor/session';
	import { PAINT_TOOL_IDS } from './PaintStage.svelte';

	let {
		session,
		state: snapshot,
		phone = false
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		phone?: boolean;
	} = $props();

	let adding = $state(false);
	let tools = $derived(snapshot.tools.filter((tool) => PAINT_TOOL_IDS.includes(tool.id)));
</script>

<div class="flex flex-wrap gap-1" role="toolbar" aria-label="Paint tools" data-paint-tools>
	{#each tools as tool (tool.id)}
		<ToolButton
			icon={tool.icon}
			label={tool.label}
			kbd={tool.key}
			compact
			position="bottom"
			pressed={tool.id === snapshot.toolId}
			onClick={() => session.setTool(tool.id)}
		/>
	{/each}
</div>
<div class="-mx-4 {phone ? 'max-h-40 overflow-y-auto' : ''}">
	<ToolPanel {session} state={snapshot} {phone} />
</div>
{#if !phone}
	<div class="relative -mx-4 border-t border-line">
		<LayersPanel {session} state={snapshot} onAddImage={() => (adding = !adding)} />
		{#if adding}
			<ImageSourceMenu mode="layer" onPick={(image) => session.addImage(image.canvas, image.name)} onClose={() => (adding = false)} />
		{/if}
	</div>
	<p class="text-sm text-fg-muted">Strokes undo one by one while Paint is open. Leaving Paint lands them as one step.</p>
{/if}
