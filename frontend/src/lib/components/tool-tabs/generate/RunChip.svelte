<script lang="ts">
	import { Button, Spinner } from '$lib/components/ui';
	import TickProgress from '$lib/components/tool-tabs/frame/TickProgress.svelte';

	let { status, progress = 0, dock = false, showCancel = true, onclick }: { status: 'queued' | 'running'; progress?: number; dock?: boolean; showCancel?: boolean; onclick: () => void } = $props();

	let percent = $derived(Math.round(progress * 100));
</script>

<div class="pointer-events-auto flex items-center gap-2 rounded-lg border py-[3px] pl-3 pr-[3px] whitespace-nowrap font-mono text-sm tabular-nums {dock ? 'border-line bg-surface-2 text-fg' : 'border-scrim-on/10 bg-scrim/70 text-scrim-on backdrop-blur'}" role="status" data-run-chip={status}>
	{#if status === 'queued'}
		<Spinner size="sm" />Waiting for a free slot
	{:else}
		Generating · {percent}%
		<TickProgress {percent} label="Generating" class="max-sm:!w-14" />
	{/if}
	{#if showCancel}
	<Button variant={dock ? 'secondary' : 'overlay'} size="xs" class="h-7" onclick={onclick} attrs={{ 'data-run-cancel': '' }}>Cancel</Button>
	{/if}
</div>
