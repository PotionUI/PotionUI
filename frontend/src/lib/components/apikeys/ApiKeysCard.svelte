<script lang="ts">
	import { Alert, Button, Card, EmptyState, Pagination, Spinner } from '$lib/components/ui';
	import LoadErrorState from '$lib/components/ui/LoadErrorState.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { ApiKey, ApiKeyLimits } from '$lib/services/api/apiKeys';
	import ApiKeyRow from './ApiKeyRow.svelte';
	import { pageOf, splitKeys, visibleKeys } from './apiKeyView';

	let {
		keys,
		limits,
		loading,
		failed,
		revokingId = null,
		onCreate,
		onEdit,
		onRevoke,
		onRetry
	}: {
		keys: ApiKey[];
		limits: ApiKeyLimits;
		loading: boolean;
		failed: boolean;
		revokingId?: string | null;
		onCreate: () => void;
		onEdit: (key: ApiKey) => void;
		onRevoke: (key: ApiKey) => void;
		onRetry: () => void;
	} = $props();

	let showInactive = $state(false);
	let requestedPage = $state(1);

	const rows = $derived(splitKeys(keys));
	const page = $derived(pageOf(visibleKeys(rows, showInactive), requestedPage));
	const keysOff = $derived(!limits.keys_enabled);

	function toggleInactive() {
		showInactive = !showInactive;
		requestedPage = 1;
	}
</script>

<Card>
	<div class="mb-3 flex flex-wrap items-start justify-between gap-3">
		<h2 class="label !mb-0">API keys</h2>
		{#if keys.length}
			<Button variant="primary" size="sm" icon="plus" disabled={keysOff} onclick={onCreate}>Create key</Button>
		{/if}
	</div>
	<p class="mb-4 text-sm text-fg-subtle">A key lets a script or an MCP client act as you, limited to what you allow.</p>

	{#if loading && !keys.length}
		<div class="flex items-center justify-center py-8"><Spinner /></div>
	{:else if failed && !keys.length}
		<LoadErrorState message="Your keys could not be loaded." {onRetry} retrying={loading} compact />
	{:else}
		{#if keysOff}
			<Alert variant="warning" density="compact" icon class="mb-3">
				API keys are turned off by your administrator. You can still revoke them.
			</Alert>
		{/if}
		{#if !keys.length}
			<EmptyState icon="key" title="No keys yet" description="Create a key for a script or an MCP client.">
				{#snippet actions()}
					<Button variant="secondary" icon="plus" disabled={keysOff} onclick={onCreate}>Create key</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<div class="divide-y divide-line overflow-hidden rounded border border-line" data-list>
				{#each page.rows as apiKey (apiKey.id)}
					<ApiKeyRow {apiKey} mcpPaused={!limits.mcp_enabled} revoking={revokingId === apiKey.id} {onEdit} {onRevoke} />
				{:else}
					<EmptyState compact icon="key" title="No active keys" />
				{/each}
			</div>
			{#if rows.inactive.length}
				<button
					type="button"
					aria-expanded={showInactive}
					class="mt-2 inline-flex items-center gap-1.5 text-sm text-fg-subtle transition-colors hover:text-fg"
					onclick={toggleInactive}
				>
					<Icon name="chevron-down" className="h-3.5 w-3.5 transition-transform {showInactive ? '' : '-rotate-90'}" />
					{showInactive ? 'Hide inactive' : `Show ${rows.inactive.length} inactive`}
				</button>
			{/if}
			{#if page.pages > 1}
				<div class="mt-3 flex justify-end">
					<Pagination currentPage={page.page} totalPages={page.pages} size="sm" onPageChange={(next) => (requestedPage = next)} />
				</div>
			{/if}
		{/if}
	{/if}
</Card>
