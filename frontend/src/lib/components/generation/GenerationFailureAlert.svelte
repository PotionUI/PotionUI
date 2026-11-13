<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { CopyButton } from '$lib/components/ui';
	import type { FailureNoticeData } from './failureNotice';

	let { notice }: { notice: FailureNoticeData } = $props();
</script>

<div class="rounded border border-danger/25 bg-danger/10 px-3 py-2.5" role="alert" data-failure-alert>
	<div class="flex items-center gap-2 text-md text-danger">
		<Icon name="warning" className="h-4 w-4 shrink-0" />
		<b class="font-semibold">{notice.title}</b>
		<CopyButton text={notice.copyText} ariaLabel="Copy error message" size="xs" class="ml-auto" />
	</div>
	<p class="mt-1 whitespace-pre-line text-base text-fg [overflow-wrap:anywhere]">{notice.message}</p>
	{#if notice.errorId}
		<div class="mt-1.5 flex items-center gap-1.5">
			<span class="whitespace-nowrap text-sm text-fg-subtle">Error ID</span>
			<span class="truncate font-mono text-sm tabular-nums text-fg">{notice.errorId}</span>
			<CopyButton text={notice.errorId} ariaLabel="Copy error ID" size="xs" />
		</div>
		{#if notice.contactAdmin}
			<p class="text-sm text-fg-subtle">Give this to your admin</p>
		{/if}
	{/if}
</div>
