<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { allChips, isChipSelected, toggleChip } from '$lib/generation/compare/axisValues';
	import type { AxisCandidate, AxisOption, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

	let {
		candidate,
		axis,
		onChange
	}: {
		candidate: AxisCandidate;
		axis: CompareAxis | null;
		tabId?: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	let selected = $derived(axis?.values ?? []);
	let options = $derived<AxisOption[]>(candidate.options.map((o) => ({ value: o.value, label: String(o.value) })));

	let groups = $derived.by(() => {
		const raw = (candidate.config.options ?? candidate.config.configuration ?? []) as unknown;
		const entries = Array.isArray(raw) ? raw : [];
		const byValue = new Map<string, string>();
		for (const entry of entries) {
			if (entry && typeof entry === 'object' && typeof (entry as Record<string, unknown>).group === 'string') {
				const record = entry as Record<string, unknown>;
				byValue.set(String(record.value), String(record.group));
			}
		}
		const order: string[] = [];
		const map = new Map<string, AxisOption[]>();
		for (const option of options) {
			const group = byValue.get(String(option.value)) ?? '';
			if (!map.has(group)) {
				map.set(group, []);
				order.push(group);
			}
			map.get(group)!.push(option);
		}
		return order.map((group) => ({ group, items: map.get(group)! }));
	});
</script>

<div class="space-y-2">
	<div>
		<button
			type="button"
			class="px-2 py-0.5 text-sm text-fg-muted underline-offset-2 hover:text-fg hover:underline"
			onclick={() => onChange(allChips(options))}
		>
			All
		</button>
	</div>
	{#each groups as entry (entry.group)}
		<div class="space-y-1">
			{#if entry.group}
				<p class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">{entry.group}</p>
			{/if}
			<div class="flex flex-wrap gap-1.5">
				{#each entry.items as option (option.value)}
					{@const on = isChipSelected(selected, option.value)}
					<button
						type="button"
						class="inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-xs tabular-nums transition-colors {on
							? 'border-signal/40 bg-signal/10 text-signal'
							: 'border-line bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
						aria-pressed={on}
						onclick={() => onChange(toggleChip(selected, options, option.value))}
					>
						{#if on}<Icon name="check" className="h-3 w-3" />{/if}
						{option.label}
					</button>
				{/each}
			</div>
		</div>
	{/each}
	<p class="font-mono text-xs uppercase tracking-wide text-fg-subtle">{selected.length} of {options.length}</p>
</div>
