<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { EmptyState, Spinner } from '$lib/components/ui';
	import LoadErrorState from '$lib/components/ui/LoadErrorState.svelte';
	import McpToolsList from '$lib/components/apikeys/McpToolsList.svelte';
	import EffectNote from './EffectNote.svelte';
	import { CEILING_LOWERED_NOTE } from './accessPanel';
	import type { CeilingDraft } from './ceilingDraft.svelte';

	let { ceiling }: { ceiling: CeilingDraft } = $props();
</script>

<DetailSection label="MCP tools">
	<div data-testid="mcp-ceiling">
		{#if ceiling.unavailable}
			<div role="status"><EmptyState compact icon="key" title="MCP tools are not available yet" /></div>
		{:else if ceiling.loading && !ceiling.listing}
			<div class="flex items-center justify-center py-8"><Spinner /></div>
		{:else if ceiling.failed && !ceiling.listing}
			<LoadErrorState message="The tool list could not be loaded." onRetry={() => ceiling.load()} retrying={ceiling.loading} compact />
		{:else if ceiling.listing}
			<McpToolsList listing={ceiling.listing} onChange={(change) => ceiling.commit(change)} />
			<EffectNote note={ceiling.lowered ? { tone: 'warning', text: CEILING_LOWERED_NOTE } : null} testid="mcp-ceiling-note" />
			<p class="mt-4 text-sm text-fg-subtle">Applies to every user. Save the page to apply it. Users keep their own choices inside this limit.</p>
		{/if}
	</div>
</DetailSection>
