<script lang="ts">
	import Button from './Button.svelte';
	import EmptyState from './EmptyState.svelte';

	let {
		title = "Couldn't load",
		message,
		onRetry,
		retrying = false,
		compact = true
	}: {
		title?: string;
		message: string;
		onRetry: () => void;
		retrying?: boolean;
		compact?: boolean;
	} = $props();
</script>

<div class="flex h-full items-center justify-center p-5">
	<EmptyState {title} description={message} icon="warning" tone="danger" {compact}>
		{#snippet actions()}
			<Button variant="secondary" size="sm" icon="refresh" loading={retrying} onclick={onRetry}>Retry</Button>
		{/snippet}
	</EmptyState>
</div>
