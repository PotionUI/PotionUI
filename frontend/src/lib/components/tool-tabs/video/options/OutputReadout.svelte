<script lang="ts">
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';
	import { fixed2 } from '$lib/tool-tabs/video/videoSteps';

	let { host, tool }: { host: VideoToolHost; tool: string } = $props();

	const NOTES: Record<string, string> = { speed: 'Playback runs at the chosen speed so you can judge it before you apply.' };

	let video = $derived(host.video);
</script>

<div class="flex items-center justify-between text-sm" data-output-readout={tool}>
	<span class="text-fg-muted">Output</span>
	<span class="font-mono tabular-nums text-success" data-speed-output={tool === 'speed' ? '' : undefined}>{fixed2(video.outputLength)} s</span>
</div>
{#if NOTES[tool]}
	<p class="text-sm text-fg-muted">{NOTES[tool]}</p>
{/if}
