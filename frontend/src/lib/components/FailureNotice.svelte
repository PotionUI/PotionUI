<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { CopyButton } from '$lib/components/ui';

	let {
		message,
		hint = '',
		errorId = null,
		centered = false
	}: {
		message: string;
		hint?: string;
		errorId?: string | null;
		centered?: boolean;
	} = $props();
</script>

<div class="space-y-1.5" data-failure-notice>
	<div
		class="flex items-start gap-2 rounded border border-warning/25 bg-warning/10 p-2 {centered ? 'justify-center text-center' : ''}"
		role="status"
	>
		<Icon name="shield" className="mt-0.5 h-4 w-4 shrink-0 text-warning" strokeWidth={1.75} />
		<div class="min-w-0 text-sm">
			<p class="font-medium text-fg">{message}</p>
			{#if hint}
				<p class="text-fg-muted">{hint}</p>
			{/if}
		</div>
	</div>
	{#if errorId}
		<div class="flex items-center gap-1.5 {centered ? 'justify-center' : ''}">
			<span class="whitespace-nowrap text-sm text-fg-subtle">Error ID</span>
			<span class="truncate font-mono text-sm tabular-nums text-fg">{errorId}</span>
			<CopyButton text={errorId} ariaLabel="Copy error ID" size="xs" />
		</div>
		<p class="text-sm text-fg-subtle {centered ? 'text-center' : ''}">Give this to your admin</p>
	{/if}
</div>
