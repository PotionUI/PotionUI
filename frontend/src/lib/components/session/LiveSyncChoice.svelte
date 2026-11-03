<script lang="ts">
	import { Button } from '$lib/components/ui';

	let {
		label,
		detail = '',
		onTheirs,
		onMine
	}: {
		label: string;
		detail?: string;
		onTheirs: () => void;
		onMine: () => void;
	} = $props();
</script>

<div class="live-choice" role="group" aria-label="Changed by {label}" data-testid="live-sync-choice">
	<span class="min-w-0 truncate text-xs text-fg-muted">
		Changed by <span class="font-medium text-signal">{label}</span>{#if detail}<span class="text-fg-subtle"> · {detail}</span>{/if}
	</span>
	<span class="flex shrink-0 items-center gap-1">
		<Button size="xs" variant="secondary" onclick={onTheirs}>Use theirs</Button>
		<Button size="xs" variant="ghost" onclick={onMine}>Keep mine</Button>
	</span>
</div>

<style>
	.live-choice {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		margin-top: 6px;
		padding: 4px 4px 4px 10px;
		border-left: 2px solid rgb(var(--signal));
		border-radius: 4px;
		background: rgb(var(--signal) / 0.06);
	}
</style>
