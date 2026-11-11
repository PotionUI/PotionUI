<script lang="ts">
	import type { ApiKeyScope } from '$lib/services/api/apiKeys';
	import KeyAccessRow from './KeyAccessRow.svelte';
	import { mcpRow, scopeRows, type DialogContext, type KeyDraft } from './apiKeyDialog';
	import type { Snippet } from 'svelte';

	let {
		draft,
		ctx,
		adminBlockedByExpiry = false,
		onScope,
		mcpExtra
	}: {
		draft: KeyDraft;
		ctx: DialogContext;
		adminBlockedByExpiry?: boolean;
		onScope: (scope: ApiKeyScope, on: boolean) => void;
		mcpExtra?: Snippet;
	} = $props();

	const rows = $derived(scopeRows(draft, ctx, { adminBlockedByExpiry }));
	const mcp = $derived(mcpRow(draft, ctx));
</script>

<div>
	<p class="label">What this key can do</p>
	<div class="divide-y divide-line rounded border border-line px-3">
		{#each rows as row (row.scope)}
			<KeyAccessRow {row} onToggle={(on) => onScope(row.scope, on)} />
		{/each}
	</div>
	<div class="mt-3">
		<div class="rounded border border-line px-3">
			<KeyAccessRow row={mcp} onToggle={(on) => onScope('mcp', on)} />
		</div>
		{@render mcpExtra?.()}
	</div>
</div>
