<script lang="ts">
	import { Button, Badge } from '$lib/components/ui';
	import { downloadStore } from '$lib/stores/downloads';
	import { isFetchDisabled, type FetchState } from './modelFetch.svelte';

	let { state, onFetch }: { state: FetchState; onFetch: () => void } = $props();
</script>

<div class="flex items-center gap-3 flex-shrink-0">
	{#if state.status === 'ready'}
		<Badge variant="success">
			Ready{#if state.size}&nbsp;&middot; {downloadStore.formatBytes(state.size)}{/if}
		</Badge>
		{#if state.loaded}
			<span title="Currently resident in memory">
				<Badge variant="signal" size="sm" dot>In memory</Badge>
			</span>
		{/if}
	{:else if state.status === 'downloading' || state.status === 'queued'}
		<span class="font-mono text-xs tabular-nums text-fg-muted"
			>{state.status === 'queued' ? 'queued' : `${Math.round(state.progress * 100)}%`}</span
		>
		<Badge variant="info">Downloading</Badge>
	{:else if state.status === 'failed'}
		<span title={state.error ?? undefined}><Badge variant="danger">Failed</Badge></span>
	{:else if state.status === 'checking'}
		<span class="text-xs text-fg-muted">Checking...</span>
	{/if}
	<Button
		size="sm"
		variant="secondary"
		disabled={isFetchDisabled(state)}
		loading={state.status === 'checking' || state.status === 'queued'}
		onclick={onFetch}
	>
		Fetch
	</Button>
</div>
