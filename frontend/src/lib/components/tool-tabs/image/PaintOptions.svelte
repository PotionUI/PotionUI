<script lang="ts">
	import ImageSourceMenu from '$lib/components/paint/ImageSourceMenu.svelte';
	import LayersPanel from '$lib/components/paint/LayersPanel.svelte';
	import ToolButton from '$lib/components/paint/ToolButton.svelte';
	import PaintActions from '$lib/components/paint/PaintActions.svelte';
	import ToolPanel from '$lib/components/paint/ToolPanel.svelte';
	import { pluginTools } from '$lib/paint/registries';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';
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
	let tools = $derived.by(() => {
		const pluginIds = new Set(pluginTools.list().map((tool) => tool.id));
		const seen = new Set<string>();
		return snapshot.tools.filter((tool) => {
			if (typeof tool.label !== 'string' || seen.has(tool.id)) return false;
			if (!PAINT_TOOL_IDS.includes(tool.id) && !pluginIds.has(tool.id)) return false;
			seen.add(tool.id);
			return true;
		});
	});
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
<PaintActions {session} />
{#if !phone}
	<div class="relative -mx-4 border-t border-line">
		<LayersPanel {session} state={snapshot} onAddImage={() => (adding = !adding)} />
		{#if adding}
			<ImageSourceMenu mode="layer" onPick={(image) => session.addImage(image.canvas, image.name)} onClose={() => (adding = false)} />
		{/if}
	</div>
	<p class="text-sm text-fg-muted">Strokes undo one by one while Paint is open. Leaving Paint lands them as one step.</p>
{/if}
