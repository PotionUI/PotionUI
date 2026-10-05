<script lang="ts">
	import { Badge } from '$lib/components/ui';
	import { formatLimitValue, percent } from '$lib/plans/format';
	import { usageState } from '$lib/plans/usage';
	import type { LimitKindDescriptor } from '$lib/plans/types';

	let {
		kind,
		used,
		limit,
		compact = false
	}: {
		kind: Pick<LimitKindDescriptor, 'value_type'>;
		used: number;
		limit: number | null;
		compact?: boolean;
	} = $props();

	const state = $derived(usageState(used, limit));
	const pct = $derived(percent(used, limit));
	const fill = $derived(state === 'full' ? 'bg-danger' : state === 'warn' ? 'bg-warning' : 'bg-signal');
</script>

<div class="flex items-center gap-2.5" data-usage-bar data-usage-state={state}>
	{#if limit !== null}
		<div class="h-1.5 {compact ? 'w-16' : 'min-w-24 flex-1'} overflow-hidden rounded bg-surface-3" role="presentation">
			<div class="h-full rounded {fill}" style="width: {pct ?? 0}%"></div>
		</div>
	{/if}
	<span class="flex items-baseline gap-1 font-mono text-xs tabular-nums text-fg">
		<span>{formatLimitValue(kind, used)}</span>
		{#if limit !== null}
			<span class="text-fg-subtle">/ {formatLimitValue(kind, limit)}</span>
		{:else if !compact}
			<span class="font-sans text-fg-subtle">no limit</span>
		{/if}
	</span>
	{#if state === 'full'}
		<Badge size="sm" variant="danger" class="font-mono">full</Badge>
	{:else if state === 'warn'}
		<Badge size="sm" variant="warning" class="font-mono">80%+</Badge>
	{/if}
</div>
