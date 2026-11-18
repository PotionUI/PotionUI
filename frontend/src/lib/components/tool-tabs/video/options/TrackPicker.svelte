<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';

	let { host }: { host: VideoToolHost } = $props();

	let video = $derived(host.video);
	let params = $derived(video.panelParams('replace_audio'));
</script>

<div class="flex flex-col gap-2" data-replace-options>
	<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Track</span>
	<div class="flex h-9 items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 text-sm" data-replace-track>
		<Icon name="editor-tool-audio" className="h-3.5 w-3.5 flex-none text-fg-muted" />
		<span class="min-w-0 flex-1 truncate {params.audio ? 'text-fg' : 'text-fg-subtle'}">{params.audio ? String(params.name || 'Audio') : 'No track picked'}</span>
	</div>
	<div class="grid grid-cols-2 gap-2">
		<Button variant="secondary" size="sm" class="h-8" icon="library" disabled={video.busy === 'audio'} onclick={() => video.pickAudio('library')} attrs={{ 'data-replace-pick': 'library' }}>Library</Button>
		<Button variant="secondary" size="sm" class="h-8" icon="upload" loading={video.busy === 'audio'} onclick={() => video.pickAudio('upload')} attrs={{ 'data-replace-pick': 'upload' }}>Upload</Button>
	</div>
</div>
