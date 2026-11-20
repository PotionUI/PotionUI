<script lang="ts">
	import ToolEntryPanel from '$lib/components/tool-tabs/frame/ToolEntryPanel.svelte';
	import { entryKey } from '$lib/editors/registry';
	import type { RailToolEntry } from '$lib/editors/railTools';
	import { LEADS } from '$lib/tool-tabs/video/entries';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';

	let {
		entry,
		host,
		phone = false,
		onclose
	}: {
		entry: RailToolEntry;
		host: VideoToolHost;
		phone?: boolean;
		onclose?: () => void;
	} = $props();

	let Lead = $derived(LEADS[entry.id]);
</script>

{#if Lead}
	<Lead {host} {phone} tool={entry.id} />
{/if}
<ToolEntryPanel {entry} entryKey={entryKey('video', entry.id)} {host} {phone} {onclose} />
