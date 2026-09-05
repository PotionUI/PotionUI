<script lang="ts">
	import { onDestroy } from 'svelte';
	import Tooltip from '../Tooltip.svelte';
	import {
		createMemoryAdvisoryController,
		type MemoryAdvisoryState
	} from '$lib/generation/memoryAdvisory';

	// Everything the estimate needs to stay current with the form the page
	// owns - this component has no opinion on where these values come from,
	// it only debounces/sequences the requests they drive (see memoryAdvisory.ts).
	export let presetId: string | null = null;
	export let mode: string | undefined = undefined;
	export let formName: string | undefined = undefined;
	export let formData: Record<string, unknown> = {};
	export let backendId: string | null | undefined = undefined;

	const controller = createMemoryAdvisoryController();
	let state: MemoryAdvisoryState = { status: 'idle', result: null, error: null };
	const unsubscribe = controller.subscribe((value) => (state = value));

	onDestroy(() => {
		unsubscribe();
		controller.dispose();
	});

	// The controller itself dedupes an unchanged input and debounces a burst
	// of changes into one request - this just reports every input change,
	// on every render, with no loop-guard needed here.
	$: controller.refresh({ presetId, mode, formName, formData, backendId });

	function formatGb(value: number): string {
		return `${value.toFixed(1)} GB`;
	}

	// `may exceed` is the only case Instrument allows `text-warning` for here -
	// never a hard red alert, and never blocking: the mark and onGenerate are
	// completely untouched by this component either way.
	$: exceedsBudget =
		state.status === 'ready' &&
		state.result != null &&
		state.result.estimate.lower_bound_gb != null &&
		state.result.budget.configured_gb != null &&
		state.result.estimate.lower_bound_gb > state.result.budget.configured_gb;

	$: line = deriveLine(state);
	$: tooltipText = state.status === 'ready' && state.result ? deriveTooltip(state.result) : '';

	function deriveLine(s: MemoryAdvisoryState): string | null {
		if (s.status === 'loading') return 'Estimating GPU memory…';
		if (s.status !== 'ready' || !s.result) return null; // idle/error stay quiet - never blocks generation

		const { estimate, coverage, device } = s.result;
		const known = coverage.known.length;
		const total = known + coverage.unknown.length;

		const estimateText =
			estimate.lower_bound_gb != null
				? `Estimated at least ~${formatGb(estimate.lower_bound_gb)} for this request (${known} of ${total} model size${total === 1 ? '' : 's'} known)`
				: total > 0
					? `Estimate unavailable: none of ${total} referenced model size${total === 1 ? '' : 's'} are indexed`
					: 'Estimate unavailable: no model references in this form';

		const deviceText =
			device.kind === 'local' && device.free_gb != null && device.total_gb != null
				? `this GPU reports ${formatGb(device.free_gb)} free of ${formatGb(device.total_gb)}`
				: device.kind === 'remote'
					? 'remote backend: memory not reported'
					: device.kind === 'unknown'
						? 'GPU visibility unknown for this backend'
						: 'no GPU reported for this backend';

		const budgetText = exceedsBudget ? ' — may exceed the configured VRAM budget' : '';

		return `${estimateText}; ${deviceText}${budgetText}.`;
	}

	function deriveTooltip(result: NonNullable<MemoryAdvisoryState['result']>): string {
		const { estimate, coverage, device, budget } = result;
		return [
			`Basis: ${estimate.basis}`,
			`Coverage: ${coverage.known.length} known, ${coverage.unknown.length} unknown`,
			`Device: ${device.provenance}`,
			`Budget: ${budget.configured_gb != null ? formatGb(budget.configured_gb) : 'not configured'} (${budget.source})`,
			...coverage.uncertainty
		].join(' · ');
	}
</script>

{#if line}
	<Tooltip text={tooltipText} position="top" delay={150} wrapperClass="inline-flex">
		<span class="memory-advisory-line {exceedsBudget ? 'text-warning' : 'text-fg-subtle'}">{line}</span>
	</Tooltip>
{/if}

<style>
	.memory-advisory-line {
		font-size: 11px;
		line-height: 1.3;
	}
</style>
