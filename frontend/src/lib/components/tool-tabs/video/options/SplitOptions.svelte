<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';
	import { formatEditorTime } from '$lib/tool-tabs/video/videoState';

	let { host }: { host: VideoToolHost } = $props();

	let video = $derived(host.video);
	let at = $derived(Number(video.panelParams('split').at ?? 0));
</script>

<div class="flex flex-col gap-2" data-split-options>
	<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Split at</span>
	<div class="flex items-center gap-2">
		<span class="flex h-9 flex-1 items-center rounded border border-line-strong bg-surface-2 px-2.5 font-mono text-base tabular-nums text-fg" data-split-at>{formatEditorTime(at)}</span>
		<Button variant="secondary" size="sm" class="h-9" onclick={() => video.setParams('split', { at: video.playhead })} attrs={{ 'data-split-playhead': '' }}>Use playhead</Button>
	</div>
</div>
<p class="text-sm text-fg-muted">Saving makes two Library items: up to this point, and from it.</p>
