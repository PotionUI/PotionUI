<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { NodeTypeDef } from '$lib/types/automations';
	import { PALETTE_DRAG_MIME } from './paletteDrag';

	let { nodeType }: { nodeType: NodeTypeDef } = $props();

	function handleDragStart(event: DragEvent) {
		if (!event.dataTransfer) return;
		event.dataTransfer.setData(PALETTE_DRAG_MIME, nodeType.key);
		event.dataTransfer.effectAllowed = 'move';
	}
</script>

<div
	class="flex items-start gap-2 px-2.5 py-2 rounded border border-line bg-surface-2 hover:bg-surface-3 hover:border-line-hover cursor-grab active:cursor-grabbing transition-colors"
	draggable="true"
	ondragstart={handleDragStart}
	role="button"
	tabindex="0"
	title={nodeType.description || nodeType.title}
>
	<Icon name={nodeType.icon || 'cube'} className="w-4 h-4 text-fg-muted flex-shrink-0 mt-0.5" />
	<div class="min-w-0 flex-1">
		<div class="flex items-center justify-between gap-2">
			<span class="text-xs font-medium text-fg truncate">{nodeType.title}</span>
			{#if nodeType.outputs && nodeType.outputs.length > 0}
				<span class="text-2xs font-mono tabular-nums text-fg-subtle flex-shrink-0"
					>{nodeType.outputs.length}</span
				>
			{/if}
		</div>
		{#if nodeType.description}
			<p class="text-2xs text-fg-subtle line-clamp-2">{nodeType.description}</p>
		{/if}
	</div>
</div>
