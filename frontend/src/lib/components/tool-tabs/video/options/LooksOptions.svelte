<script lang="ts">
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';
	import LooksTiles from './LooksTiles.svelte';
	import PreviewClip from './PreviewClip.svelte';

	let { host, phone = false, tool }: { host: VideoToolHost; phone?: boolean; tool: string } = $props();
</script>

<LooksTiles {host} {phone} />
<PreviewClip {host} {tool} />
