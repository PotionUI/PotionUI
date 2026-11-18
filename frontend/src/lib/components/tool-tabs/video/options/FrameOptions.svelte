<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';
	import { formatEditorTime } from '$lib/tool-tabs/video/videoState';

	let { host }: { host: VideoToolHost } = $props();

	let video = $derived(host.video);
</script>

<div class="flex flex-col gap-2" data-frame-options>
	<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Frame at</span>
	<span class="flex h-9 items-center rounded border border-line-strong bg-surface-2 px-2.5 font-mono text-base tabular-nums text-fg" data-frame-at>{formatEditorTime(video.playhead)}</span>
</div>
<div class="flex flex-col gap-2">
	<Button variant="secondary" size="sm" class="h-8" icon="image" loading={video.busy === 'frame-save'} disabled={!!video.busy} onclick={() => video.frameAction('save')} attrs={{ 'data-frame-action': 'save' }}>Save frame as image</Button>
	<Button variant="secondary" size="sm" class="h-8" icon="sliders-horizontal" loading={video.busy === 'frame-open'} disabled={!!video.busy} onclick={() => video.frameAction('open')} attrs={{ 'data-frame-action': 'open' }}>Open frame in Image editor</Button>
</div>
<p class="text-sm text-fg-muted">Uses the paused frame with your edits applied.</p>
