<script lang="ts">
	import { onMount } from 'svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import ValuePreview from './ValuePreview.svelte';
	import {
		clampNumber,
		formatNumber,
		numberAxisValues,
		parseNumberList,
		rangeValues
	} from '$lib/generation/compare/axisValues';

	let {
		min,
		max,
		step,
		initial,
		onValues,
		emitOnMount = true
	}: {
		min: number | null;
		max: number | null;
		step: number;
		initial: number;
		onValues: (values: number[]) => void;
		emitOnMount?: boolean;
	} = $props();

	const start = (() => {
		const seed = clampNumber(initial, min, max);
		return {
			from: formatNumber(seed, step),
			to: formatNumber(clampNumber(seed + step * 4, min, max), step),
			stride: formatNumber(step)
		};
	})();
	let mode = $state<'list' | 'range'>('range');
	let from = $state(start.from);
	let to = $state(start.to);
	let stride = $state(start.stride);
	let listText = $state(start.from);
	let numbers = $state<number[]>([]);

	function recompute() {
		numbers =
			mode === 'range'
				? rangeValues(Number(from), Number(to), Number(stride), min, max)
				: parseNumberList(listText, min, max);
		onValues(numbers);
	}

	onMount(() => {
		if (emitOnMount) recompute();
	});

	let preview = $derived(numberAxisValues(numbers, step));
</script>

<div class="space-y-2">
	<div class="flex items-center justify-between gap-2">
		<SegmentedControl
			variant="toggle"
			ariaLabel="Value input"
			items={[
				{ id: 'list', label: 'List' },
				{ id: 'range', label: 'Range' }
			]}
			selected={mode}
			onSelect={(id) => {
				mode = id as 'list' | 'range';
				recompute();
			}}
		/>
		<span class="font-mono text-xs tabular-nums text-fg-subtle" data-testid="value-count">{numbers.length} values</span>
	</div>
	{#if mode === 'range'}
		<div class="grid grid-cols-3 gap-2">
			<label class="block">
				<span class="mb-1 block font-mono text-2xs uppercase tracking-wide text-fg-subtle">From</span>
				<input class="input font-mono tabular-nums" type="number" inputmode="decimal" aria-label="From" bind:value={from} oninput={recompute} />
			</label>
			<label class="block">
				<span class="mb-1 block font-mono text-2xs uppercase tracking-wide text-fg-subtle">To</span>
				<input class="input font-mono tabular-nums" type="number" inputmode="decimal" aria-label="To" bind:value={to} oninput={recompute} />
			</label>
			<label class="block">
				<span class="mb-1 block font-mono text-2xs uppercase tracking-wide text-fg-subtle">Step</span>
				<input class="input font-mono tabular-nums" type="number" inputmode="decimal" aria-label="Step" bind:value={stride} oninput={recompute} />
			</label>
		</div>
	{:else}
		<input class="input font-mono tabular-nums" type="text" aria-label="Values" placeholder="12, 18, 24" bind:value={listText} oninput={recompute} />
	{/if}
	<ValuePreview values={preview} />
	{#if min !== null || max !== null}
		<p class="font-mono text-xs text-fg-subtle">
			Allowed {min !== null ? formatNumber(min, step) : '...'} to {max !== null ? formatNumber(max, step) : '...'}
		</p>
	{/if}
</div>
