<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import ValuePreview from './ValuePreview.svelte';
	import { MAX_SEED, randomSeeds, seedAxisValues } from '$lib/generation/compare/axisValues';
	import { MAX_AXIS_VALUES, type AxisCandidate, type CompareAxis, type CompareAxisValue } from '$lib/generation/compare/types';

	let {
		axis,
		onChange
	}: {
		candidate?: AxisCandidate;
		axis: CompareAxis | null;
		tabId?: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	let mode = $state<'list' | 'random'>('random');
	const start = (() => ({
		count: axis && axis.values.length > 0 ? axis.values.length : 3,
		listText: axis ? axis.values.map((v) => String(v.value)).join(', ') : '',
		values: axis?.values ?? []
	}))();
	let count = $state(start.count);
	let listText = $state(start.listText);
	let values = $state<CompareAxisValue[]>(start.values);

	function emit(next: CompareAxisValue[]) {
		values = next;
		onChange(next);
	}

	function reroll() {
		const clamped = Math.max(1, Math.min(Math.floor(Number(count)) || 1, MAX_AXIS_VALUES));
		count = clamped;
		emit(seedAxisValues(randomSeeds(clamped)));
	}

	function parseList() {
		const seen = new Set<number>();
		for (const token of listText.split(/[\s,;]+/)) {
			const parsed = Number(token);
			if (token && Number.isInteger(parsed) && parsed >= 0 && parsed <= MAX_SEED) seen.add(parsed);
			if (seen.size >= MAX_AXIS_VALUES) break;
		}
		emit(seedAxisValues([...seen]));
	}

	function bump(delta: number) {
		count = Math.max(1, Math.min((Number(count) || 1) + delta, MAX_AXIS_VALUES));
		reroll();
	}

	onMount(() => {
		if (start.values.length === 0) reroll();
	});
</script>

<div class="space-y-2">
	<div class="flex items-center justify-between gap-2">
		<SegmentedControl
			variant="toggle"
			ariaLabel="Seed input"
			items={[
				{ id: 'list', label: 'List' },
				{ id: 'random', label: 'Random' }
			]}
			selected={mode}
			onSelect={(id) => {
				mode = id as 'list' | 'random';
				if (mode === 'random') reroll();
				else parseList();
			}}
		/>
		<span class="font-mono text-xs tabular-nums text-fg-subtle" data-testid="value-count">{values.length} values</span>
	</div>
	{#if mode === 'random'}
		<div class="flex items-center gap-2">
			<div class="flex items-center rounded border border-line bg-surface-2">
				<input
					class="w-14 bg-transparent px-2 py-1.5 font-mono text-sm tabular-nums text-fg focus:outline-none"
					type="number"
					min="1"
					max={MAX_AXIS_VALUES}
					aria-label="Number of random seeds"
					bind:value={count}
					oninput={reroll}
				/>
				<button type="button" class="px-2 text-fg-muted hover:text-fg" aria-label="Fewer seeds" onclick={() => bump(-1)}>-</button>
				<button type="button" class="px-2 text-fg-muted hover:text-fg" aria-label="More seeds" onclick={() => bump(1)}>+</button>
			</div>
			<p class="text-xs text-fg-muted">random seeds, picked now and saved in each cell</p>
		</div>
		<div class="flex flex-wrap items-center gap-1.5">
			<ValuePreview {values} limit={6} />
			<button
				type="button"
				class="inline-flex items-center gap-1 rounded border border-line bg-surface-2 px-2 py-0.5 text-xs text-fg-muted hover:border-line-hover hover:text-fg"
				onclick={reroll}
			>
				<Icon name="refresh" className="h-3 w-3" />
				Reroll
			</button>
		</div>
	{:else}
		<input class="input font-mono tabular-nums" type="text" aria-label="Seeds" placeholder="4211984, 7, 90210" bind:value={listText} oninput={parseList} />
		<ValuePreview {values} limit={6} />
	{/if}
	<p class="text-xs text-fg-muted">Lock seed is off while seed is an axis.</p>
</div>
