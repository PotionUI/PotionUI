<script lang="ts">
	import { Button } from '$lib/components/ui';
	import { errorText, filterCatalog, updateMineFilter } from '$lib/filters/catalog';
	import { selectFilterItem } from '$lib/filters/actions';
	import { parsePoints, type CurvePoint } from '$lib/filters/curve';
	import {
		countEdits,
		editedParams,
		isStepEnabled,
		opLookup,
		pointParams,
		scalarParams,
		serializeSteps,
		stepValue
	} from '$lib/filters/steps';
	import { toasts } from '$lib/stores/toast';
	import CurvePlot from './CurvePlot.svelte';
	import { PAINT_ICONS } from './icons';
	import type { PaintSession, SessionSnapshot } from './session';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let onSaveFilter: () => void = () => {};

	let updating = false;

	$: active = state.filter.active;
	$: lookup = opLookup($filterCatalog.ops);
	$: steps = state.filter.steps;
	$: edited = active ? countEdits(active.steps, steps, lookup) : 0;
	$: layer = state.layers[state.activeIndex];
	$: lutBlocked = !!active?.hasLut;

	function number(event: Event): number {
		return Number((event.currentTarget as HTMLInputElement).value);
	}

	function plots(index: number): Array<{ id: string; points: CurvePoint[] }> {
		const def = lookup(steps[index].op);
		const result: Array<{ id: string; points: CurvePoint[] }> = [];
		for (const param of pointParams(def)) {
			const points = parsePoints(steps[index][param.id]);
			if (points) result.push({ id: param.id, points });
		}
		return result;
	}

	async function update() {
		if (!active || updating || lutBlocked) return;
		updating = true;
		try {
			const updated = await updateMineFilter(active.id, {
				steps: serializeSteps(steps),
				intensity: state.filter.intensity
			});
			toasts.success(`Updated ${active.name}`);
			await selectFilterItem(session, updated);
		} catch (error) {
			toasts.error(errorText(error, 'The filter could not be updated.'));
		} finally {
			updating = false;
		}
	}
</script>

{#if active}
	<div class="flex flex-col gap-3 p-3">
		<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Adjust</p>

		<div class="flex items-center justify-between gap-2">
			<button
				type="button"
				class="inline-flex items-center gap-1 rounded text-xs text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal"
				on:click={() => session.closeFineTune()}
			>
				<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 5l-7 7 7 7" />
				</svg>
				Filters
			</button>
			<p class="min-w-0 truncate text-xs">
				<span class="font-semibold text-fg">{active.name}</span>
				<span class="font-mono tabular-nums text-fg-subtle">{edited} {edited === 1 ? 'tweak' : 'tweaks'}</span>
			</p>
		</div>

		{#each steps as step, index (index)}
			{@const def = lookup(step.op)}
			{@const enabled = isStepEnabled(step)}
			{@const changed = editedParams(active.steps[index], step, lookup) > 0}
			<section class="flex flex-col gap-2 border-t border-line pt-3" aria-label={def?.label ?? step.op}>
				<div class="flex items-center gap-2">
					<h3 class="min-w-0 flex-1 truncate text-xs font-semibold {enabled ? 'text-fg' : 'text-fg-subtle'}">
						{def?.label ?? step.op}
					</h3>
					{#if changed}
						<span class="h-1.5 w-1.5 rounded-full bg-signal" role="img" aria-label="Edited"></span>
					{/if}
					<button
						type="button"
						aria-pressed={enabled}
						aria-label="{enabled ? 'Disable' : 'Enable'} {def?.label ?? step.op}"
						class="inline-flex h-6 w-6 items-center justify-center rounded text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal"
						on:click={() => session.toggleFilterStep(index)}
					>
						<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="1.8"
								d={enabled ? PAINT_ICONS.eye : PAINT_ICONS.eyeOff}
							/>
						</svg>
					</button>
				</div>

				{#if !def}
					<p class="text-xs text-fg-subtle">This step is not available.</p>
				{:else}
					<div class="flex flex-col gap-2 {enabled ? '' : 'opacity-50'}">
						{#each scalarParams(def) as param (param.id)}
							<div class="flex items-center gap-2">
								<label for="finetune-{index}-{param.id}" class="w-20 shrink-0 text-xs text-fg-muted">{param.label}</label>
								<input
									id="finetune-{index}-{param.id}"
									type="range"
									min={param.min}
									max={param.max}
									step={param.type === 'float' ? 0.1 : 1}
									value={stepValue(step, param)}
									class="h-6 min-w-0 flex-1 accent-signal"
									on:input={(event) => session.setFilterStepParam(index, param.id, number(event))}
								/>
								<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">
									{stepValue(step, param)}{param.unit ?? ''}
								</span>
							</div>
						{/each}
						{#if pointParams(def).length > 0}
							<CurvePlot curves={plots(index)} label={def.label} />
						{/if}
					</div>
				{/if}
			</section>
		{/each}

		<p class="text-xs leading-relaxed text-fg-subtle">
			Applies to <span class="font-semibold text-fg-muted">{layer?.name ?? 'the layer'}</span>{state.hasSelection
				? ' inside the selection'
				: ''}. Nothing changes until Apply.
		</p>

		<div class="flex items-center gap-2">
			<div class="flex-1">
				<Button variant="primary" size="sm" icon="check" class="w-full" disabled={state.filter.intensity === 0} onclick={() => session.applyFilterToLayer()}>
					Apply
				</Button>
			</div>
			<Button variant="secondary" size="sm" disabled={edited === 0} onclick={() => session.resetFilterSteps()}>
				Reset steps
			</Button>
		</div>

		<div class="flex flex-wrap items-center gap-2 border-t border-line pt-3">
			{#if active.owned}
				<Button variant="secondary" size="sm" icon="save" disabled={updating || lutBlocked || edited === 0} onclick={update}>
					Update {active.name}
				</Button>
				<Button variant="secondary" size="sm" disabled={lutBlocked} onclick={onSaveFilter}>
					<span aria-label="Save as new filter">Save as new</span>
				</Button>
			{:else}
				<Button variant="secondary" size="sm" icon="save" disabled={lutBlocked} title={lutBlocked ? "LUT filters can't be copied yet" : undefined} onclick={onSaveFilter}>
					Save as filter
				</Button>
			{/if}
		</div>
		{#if lutBlocked}
			<p class="text-xs leading-relaxed text-fg-subtle" data-testid="lut-refusal">
				LUT filters can't be copied yet. Saved filters hold adjustment steps only.
			</p>
		{/if}
	</div>
{/if}
