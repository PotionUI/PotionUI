<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';

	let { host, tool }: { host: VideoToolHost; tool: string } = $props();

	let video = $derived(host.video);
	let measured = $derived(!!video.effects.get(tool)?.block.analysis);
</script>

{#if video.showClip}
	{#if video.previewState === 'rendering'}
		<Button variant="secondary" size="sm" class="h-8 w-full" icon="loading" onclick={() => video.cancelPreview()} attrs={{ 'data-preview-clip': 'rendering' }}>
			Rendering preview · Cancel
		</Button>
	{:else}
		<Button variant="secondary" size="sm" class="h-8 w-full" icon="play" onclick={() => video.previewClip()} attrs={{ 'data-preview-clip': video.previewState }}>
			Preview 3 s <span class="font-mono text-sm text-fg-subtle">480p</span>
		</Button>
	{/if}
	{#if measured}
		<p class="text-sm text-fg-muted" data-measure-note>Colours are measured on the server, so the live preview waits. Preview 3 s renders a short clip with the result.</p>
	{/if}
{/if}
