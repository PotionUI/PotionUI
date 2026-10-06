<script lang="ts">
	import { Button } from '$lib/components/ui';
	import CurvePlot from './CurvePlot.svelte';
	import FineTunePanel from './FineTunePanel.svelte';
	import type { PaintSession, SessionSnapshot } from './session';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let onSaveFilter: () => void = () => {};

	$: layer = state.layers[state.activeIndex];

	function number(event: Event): number {
		return Number((event.currentTarget as HTMLInputElement).value);
	}
</script>

{#if state.filter.fineTune}
	<FineTunePanel {session} {state} {onSaveFilter} />
{:else}
<div class="flex flex-col gap-3 p-3">
	<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Adjust</p>

	{#each state.filters.filter((filter) => !filter.toggle) as filter (filter.id)}
		<div class="flex flex-col gap-2">
			{#if state.filters.filter((f) => !f.toggle).length > 1}
				<p class="text-xs font-semibold text-fg-muted">{filter.label}</p>
			{/if}
			{#each filter.params as param (param.id)}
				<div class="flex items-center gap-2">
					<label for="adjust-{filter.id}-{param.id}" class="w-20 shrink-0 text-xs text-fg-muted">
						{param.label}
					</label>
					<input
						id="adjust-{filter.id}-{param.id}"
						type="range"
						min={param.min}
						max={param.max}
						step={param.step ?? 1}
						value={state.adjustValues[filter.id]?.[param.id] ?? param.value}
						class="flex-1 min-w-0 h-6 accent-signal"
						on:input={(event) => session.setAdjustValue(filter.id, param.id, number(event))}
					/>
					<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">
						{state.adjustValues[filter.id]?.[param.id] ?? param.value}{param.unit ?? ''}
					</span>
				</div>
			{/each}
			{#if filter.plot}
				<CurvePlot label={filter.label} />
			{/if}
		</div>
	{/each}

	<div class="flex flex-wrap gap-1.5">
		{#each state.filters.filter((filter) => filter.toggle) as filter (filter.id)}
			<button
				type="button"
				aria-pressed={state.adjustValues[filter.id]?.on === true}
				class="h-8 px-3 rounded border text-xs transition-colors {state.adjustValues[filter.id]?.on === true
					? 'border-signal/60 bg-signal/10 text-signal'
					: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
				on:click={() =>
					session.setAdjustValue(filter.id, 'on', state.adjustValues[filter.id]?.on !== true)}
			>
				{filter.label}
			</button>
		{/each}
	</div>

	<p class="text-xs leading-relaxed text-fg-subtle">
		Applies to <span class="font-semibold text-fg-muted">{layer?.name ?? 'the layer'}</span>{state.hasSelection
			? ' inside the selection'
			: ''}. Nothing changes until Apply.
	</p>

	<div class="flex items-center gap-2">
		<div class="flex-1">
			<Button
				variant="primary"
				size="sm"
				icon="check"
				class="w-full"
				disabled={!state.adjustActive}
				onclick={() => session.applyAdjust()}>Apply</Button
			>
		</div>
		<Button variant="secondary" size="sm" disabled={!state.adjustActive} onclick={() => session.resetAdjust()}>
			Reset
		</Button>
	</div>
</div>
{/if}
