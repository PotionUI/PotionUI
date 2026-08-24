<script lang="ts">
	import { Button } from '$lib/components/ui';

	let {
		offset,
		limit,
		total,
		onPrev,
		onNext
	}: {
		offset: number;
		limit: number;
		total: number;
		onPrev: () => void;
		onNext: () => void;
	} = $props();
</script>

<div class="p-2 flex items-center justify-between">
	<Button variant="ghost" size="xs" disabled={offset === 0} onclick={onPrev}>Prev</Button>
	<span class="text-2xs font-mono tabular-nums text-fg-subtle">
		{total === 0 ? 0 : offset + 1}-{Math.min(offset + limit, total)} / {total}
	</span>
	<Button variant="ghost" size="xs" disabled={offset + limit >= total} onclick={onNext}>Next</Button>
</div>
