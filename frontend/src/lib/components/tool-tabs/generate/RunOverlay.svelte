<script lang="ts">
	import type { NormRect } from '$lib/services/api/editorTasks';

	let { rect, preview = null, waiting = false }: { rect: NormRect | null; preview?: string | null; waiting?: boolean } = $props();
</script>

{#if rect}
	<div
		class="pointer-events-none absolute overflow-hidden {waiting ? 'bg-scrim/40' : ''}"
		style="left: {rect.x * 100}%; top: {rect.y * 100}%; width: {rect.width * 100}%; height: {rect.height * 100}%"
		data-run-overlay
	>
		{#if preview}
			<img src={preview} alt="" class="h-full w-full object-fill" draggable="false" data-run-preview />
		{:else}
			<div class="h-full w-full bg-scrim/40"></div>
		{/if}
	</div>
{/if}
