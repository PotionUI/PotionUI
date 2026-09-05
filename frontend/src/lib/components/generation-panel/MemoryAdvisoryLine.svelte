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
	// Set by the page when its OWN Director-request assembly (requestAssembly.ts,
	// the same pure helper Generate calls) reports the request isn't ready to
	// submit yet (a Video Director document not ready, or a continuation shot
	// whose predecessor output isn't available) - never sent to the network in
	// that case; the request could not even be BUILT, so there is nothing for
	// the server to estimate against.
	export let unresolvedReason: string | null = null;

	const controller = createMemoryAdvisoryController();
	let state: MemoryAdvisoryState = { status: 'idle', result: null, error: null };
	const unsubscribe = controller.subscribe((value) => (state = value));

	onDestroy(() => {
		unsubscribe();
		controller.dispose();
	});

	// The controller itself dedupes an unchanged input and debounces a burst
	// of changes into one request - this just reports every input change,
	// on every render, with no loop-guard needed here. An unresolved request
	// resets the controller to idle (never issues a network call) - its own
	// line is rendered directly below, bypassing `deriveLine`/`state` entirely.
	$: controller.refresh(unresolvedReason ? { presetId: null, formData: {} } : { presetId, mode, formName, formData, backendId });

	function formatGb(value: number): string {
		return `${value.toFixed(1)} GB`;
	}

	// `may be higher` is the only case Instrument allows `text-warning` for
	// here - never a hard red alert, and never blocking: the mark and
	// onGenerate are completely untouched by this component either way.
	$: exceedsBudget =
		state.status === 'ready' &&
		state.result != null &&
		state.result.estimate.checkpoint_estimate_gb != null &&
		state.result.budget.configured_gb != null &&
		state.result.estimate.checkpoint_estimate_gb > state.result.budget.configured_gb;

	$: line = unresolvedReason
		? `Estimate unavailable: the request could not be resolved (${unresolvedReason})`
		: deriveLine(state);
	$: tooltipText = !unresolvedReason && state.status === 'ready' && state.result ? deriveTooltip(state.result) : '';

	function deriveLine(s: MemoryAdvisoryState): string | null {
		if (s.status === 'loading') return 'Estimating GPU memory…';
		if (s.status !== 'ready' || !s.result) return null; // idle/error stay quiet - never blocks generation

		const { estimate, coverage, device } = s.result;
		const known = coverage.known.length;
		const total = known + coverage.unknown.length;

		// A request that itself couldn't be resolved (an in-progress or
		// invalid Video/Music Director document, or any other pipeline-build
		// failure) is never "no model references" - the form may carry
		// plenty of them, they just couldn't be walked. See
		// `GenerationOrchestrator.preview_memory`'s docstring.
		const estimateText = !coverage.active_set_resolved
			? 'Estimate unavailable: the request could not be resolved'
			: estimate.checkpoint_estimate_gb != null
				? `Checkpoint-based estimate ~${formatGb(estimate.checkpoint_estimate_gb)} (${known} of ${total} model size${total === 1 ? '' : 's'} known); runtime use may be lower with quantization or streaming and higher for uncounted components`
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

		const budgetText = exceedsBudget ? ' — may be higher than the configured VRAM budget' : '';

		return `${estimateText}; ${deviceText}${budgetText}.`;
	}

	function deriveTooltip(result: NonNullable<MemoryAdvisoryState['result']>): string {
		const { estimate, coverage, device, budget } = result;
		const parts = [
			`Basis: ${estimate.basis}`,
			`Coverage: ${coverage.known.length} known, ${coverage.unknown.length} unknown`,
			`Device: ${device.provenance}`,
			// `budget.configured_gb` is the BACKEND's whole-request budget only -
			// never a per-pipe hint folded in (see budget.pipe_hints_gb below).
			`Backend budget: ${budget.configured_gb != null ? formatGb(budget.configured_gb) : 'not configured'} (${budget.source})`
		];
		if (budget.pipe_hints_gb.length > 0) {
			// Per-STAGE hints, distinct from the backend-wide budget above - a
			// preset can declare several with different values, so none of
			// them is presented as if it were the whole request's budget.
			const hints = budget.pipe_hints_gb
				.map((h) => `${h.pipe}=${formatGb(h.hint_gb)}${h.composed_gb != null ? ` (effective ${formatGb(h.composed_gb)})` : ''}`)
				.join(', ');
			parts.push(`Per-stage hints (not merged into the budget above): ${hints}`);
		}
		parts.push(...coverage.uncertainty);
		return parts.join(' · ');
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
