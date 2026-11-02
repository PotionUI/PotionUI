<script lang="ts">
	import ToolForm from '$lib/media-tools/ToolForm.svelte';
	import { paramIssues } from '$lib/media-tools/params';
	import type { ToolDTO } from '$lib/media-tools/types';

	let {
		tool,
		values,
		note = null,
		onchange
	}: {
		tool: ToolDTO | null;
		values: Record<string, unknown>;
		note?: string | null;
		onchange: (next: Record<string, unknown>) => void;
	} = $props();

	let errors = $derived(tool ? paramIssues(tool.params, values) : {});
</script>

{#if tool}
	{#if tool.params.length}
		<div class="flex flex-col gap-4" data-catalog-tool={tool.id}>
			<ToolForm params={tool.params} {values} {errors} onChange={(name, value) => onchange({ ...values, [name]: value })} />
		</div>
	{:else}
		<p class="text-sm text-fg-muted">{tool.label} has no settings. Apply adds it as a step.</p>
	{/if}
	{#if note}<p class="text-sm text-fg-muted">{note}</p>{/if}
{:else}
	<p class="text-sm text-fg-muted">This tool is not available right now.</p>
{/if}
