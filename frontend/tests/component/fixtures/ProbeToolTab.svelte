<script lang="ts">
	import type { ToolTabHost } from '$lib/tool-tabs/types';

	let { host }: { host: ToolTabHost } = $props();

	let name = $derived(host.source?.url?.split('/').pop()?.replace(/\.[^.]+$/, '') ?? null);

	$effect(() => {
		if (name) {
			host.setTitle(name);
			host.setThumbnail(host.source?.url ?? null);
		}
	});
</script>

<div data-testid="probe-tool" data-tool={host.tool}>
	{#if host.source}
		<img src={host.source.url} alt="" data-testid="probe-source" />
	{:else}
		<p data-testid="probe-empty">Nothing opened</p>
	{/if}
</div>
