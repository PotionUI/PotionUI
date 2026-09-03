<script lang="ts">
	// The router's decision trace (migration 009) for this generation - chosen
	// backend + reason up front, other candidates (kept or dropped) with their
	// reasons, and the rule-by-rule trace behind a collapsed disclosure, same
	// idiom as GenerationStatusLog's collapsed-by-default detail.
	import { Badge } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { RoutingDecision } from '$lib/types/history';

	let { routing }: { routing: RoutingDecision } = $props();

	let otherCandidates = $derived(
		routing.candidates.filter((c) => c.backend_id !== routing.chosen.backend_id)
	);
	let expanded = $state(false);
</script>

<div class="bg-surface-1 border border-line rounded-lg overflow-hidden">
	<div class="flex items-center gap-2 px-4 sm:px-5 py-3">
		<Icon name="server" className="w-4 h-4 text-signal" />
		<h3 class="text-sm font-medium text-fg">Routing</h3>
	</div>

	<div class="px-4 sm:px-5 pb-4 space-y-2">
		<div class="flex items-center justify-between gap-2">
			<span class="font-mono text-xs font-semibold text-fg truncate">{routing.chosen.backend_name}</span>
			<Badge variant="signal" size="sm">chosen</Badge>
		</div>
		{#if routing.chosen.reason}
			<p class="text-xs text-fg-muted leading-relaxed">{routing.chosen.reason}</p>
		{/if}

		{#if otherCandidates.length > 0}
			<div class="divide-y divide-line border border-line rounded">
				{#each otherCandidates as candidate (candidate.backend_id)}
					<div
						class="flex items-start justify-between gap-2 px-2 py-1.5 {candidate.dropped
							? 'text-fg-subtle'
							: 'text-fg-muted'}"
					>
						<span class="font-mono text-2xs truncate">{candidate.backend_name}</span>
						<span class="text-2xs text-right truncate max-w-[60%]">
							{candidate.reasons[candidate.reasons.length - 1] ?? ''}
						</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	{#if routing.rule_trace.length > 0}
		<button
			type="button"
			class="w-full flex items-center justify-between gap-3 px-4 sm:px-5 py-3 text-left border-t border-line hover:bg-surface-2/40 transition-colors duration-100"
			onclick={() => (expanded = !expanded)}
			aria-expanded={expanded}
		>
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Rule trace</span>
			<Icon
				name="chevron-right"
				className="w-3.5 h-3.5 text-fg-subtle transition-transform duration-150 {expanded ? 'rotate-90' : ''}"
			/>
		</button>

		{#if expanded}
			<div class="px-4 sm:px-5 pb-4 pt-1 overflow-x-auto border-t border-line">
				<table class="w-full font-mono text-2xs tabular-nums">
					<thead>
						<tr class="text-fg-disabled uppercase tracking-wider">
							<th class="text-left font-medium pb-1">Rule</th>
							<th class="text-right font-medium pb-1">Before → After</th>
							<th class="text-right font-medium pb-1">ms</th>
						</tr>
					</thead>
					<tbody>
						{#each routing.rule_trace as entry (entry.rule)}
							<tr class="text-fg-muted">
								<td class="py-0.5">{entry.rule}</td>
								<td class="py-0.5 text-right">{entry.before} → {entry.after}</td>
								<td class="py-0.5 text-right">{entry.ms}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{/if}
</div>
