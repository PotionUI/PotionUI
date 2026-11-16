<script lang="ts">
	import { Button, Spinner } from '$lib/components/ui';
	import TickProgress from '$lib/components/tool-tabs/frame/TickProgress.svelte';

	let { status, progress = 0, onclick }: { status: 'queued' | 'running'; progress?: number; onclick: () => void } = $props();

	let percent = $derived(Math.round(progress * 100));
</script>

<div class="pointer-events-auto flex items-center gap-2 rounded-lg border border-scrim-on/10 bg-scrim/70 py-[3px] pl-3 pr-[3px] whitespace-nowrap font-mono text-sm tabular-nums text-scrim-on backdrop-blur" role="status" data-run-chip={status}>
	{#if status === 'queued'}
		<Spinner size="sm" />Waiting for a free slot
	{:else}
		Generating · {percent}%
		<TickProgress {percent} label="Generating" class="max-sm:!w-14" />
	{/if}
	<Button variant="overlay" size="xs" class="h-7" onclick={onclick} attrs={{ 'data-run-cancel': '' }}>Cancel</Button>
</div>
