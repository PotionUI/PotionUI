<script lang="ts">
	import type { NormRect } from '$lib/services/api/editorTasks';

	let { rect }: { rect: NormRect | null } = $props();
</script>

{#if rect}
	<div
		class="pointer-events-none absolute rounded border border-dashed border-warning"
		style="left: {rect.x * 100}%; top: {rect.y * 100}%; width: {rect.width * 100}%; height: {rect.height * 100}%"
		data-stale-outline
	></div>
{/if}
