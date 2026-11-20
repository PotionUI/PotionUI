<script lang="ts">
	import EntryForm from '$lib/components/tool-tabs/frame/EntryForm.svelte';
	import { paramIssues } from '$lib/media-tools/params';
	import { toolParamsToSchema } from '$lib/media-tools/toolSchema';
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
	let fields = $derived(tool ? toolParamsToSchema(tool.params) : []);
</script>

{#if tool}
	{#if tool.params.length}
		<div data-catalog-tool={tool.id}>
			<EntryForm {fields} {values} {errors} label="{tool.label} options" onValues={(next, info) => !info.commit && onchange(next)} />
		</div>
	{:else}
		<p class="text-sm text-fg-muted">{tool.label} has no settings. Apply adds it as a step.</p>
	{/if}
	{#if note}<p class="text-sm text-fg-muted">{note}</p>{/if}
{:else}
	<p class="text-sm text-fg-muted">This tool is not available right now.</p>
{/if}
