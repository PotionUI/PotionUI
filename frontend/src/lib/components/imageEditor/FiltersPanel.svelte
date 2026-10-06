<script lang="ts">
	import { Button } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { canApply } from '$lib/filters/toolState';
	import FilterStrip from './FilterStrip.svelte';
	import { PAINT_ICONS } from './icons';
	import type { PaintSession, SessionSnapshot } from './session';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let phone = false;
	export let onSaveFilter: () => void = () => {};

	$: active = state.filter.active;
	$: layer = state.layers[state.activeIndex];
	$: applicable = canApply({ active, intensity: state.filter.intensity, compare: false });
	$: originLabel = !active
		? ''
		: active.source === 'mine'
			? 'only you'
			: active.source === 'plugin'
				? `Plugin ${active.pluginId ?? ''}`.trim()
				: active.source === 'local'
					? 'Local'
					: 'Built in';
	$: stepLabel = active ? `${active.steps.length} ${active.steps.length === 1 ? 'step' : 'steps'}` : '';
	$: lutBlocked = !!active?.hasLut;

	function intensityFrom(event: Event): number {
		return Number((event.currentTarget as HTMLInputElement).value);
	}

	function holdCompare(on: boolean) {
		session.setFilterCompare(on);
	}

	function compareKey(event: KeyboardEvent, on: boolean) {
		if (event.key !== ' ' && event.key !== 'Enter') return;
		event.preventDefault();
		event.stopPropagation();
		if (event.repeat) return;
		holdCompare(on);
	}
</script>

<div class="flex flex-col gap-3 p-3 max-md:p-0">
	<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle max-md:hidden">Filters</p>

	{#if phone}
		<FilterStrip {session} {state} />
	{/if}

	<div class="flex flex-col gap-3 max-md:px-3 max-md:pb-3">
		{#if active}
			<div class="flex items-start gap-2">
				<div class="min-w-0 flex-1">
					<p class="flex items-baseline gap-2">
						<span class="truncate text-sm font-semibold text-fg" data-testid="filter-name">{active.name}</span>
						<span class="shrink-0 text-xs text-fg-muted">{active.group}</span>
					</p>
					<p class="text-xs text-fg-subtle">{originLabel} · {stepLabel}</p>
				</div>
				<Tooltip text="Hold to compare with the original" kbd="\" position="left" wrapperClass="shrink-0">
					<button
						type="button"
						aria-label="Compare with the original"
						aria-pressed={state.filter.compare}
						class="inline-flex h-8 w-8 items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-muted transition-colors hover:border-line-hover hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal touch-manipulation {state.filter.compare ? 'border-signal/60 bg-signal/10 text-signal' : ''}"
						on:pointerdown={() => holdCompare(true)}
						on:pointerup={() => holdCompare(false)}
						on:pointerleave={() => holdCompare(false)}
						on:pointercancel={() => holdCompare(false)}
						on:blur={() => holdCompare(false)}
						on:keydown={(event) => compareKey(event, true)}
						on:keyup={(event) => compareKey(event, false)}
					>
						<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="1.8"
								d={state.filter.compare ? PAINT_ICONS.eyeOff : PAINT_ICONS.eye}
							/>
						</svg>
					</button>
				</Tooltip>
			</div>
		{:else}
			<p class="text-xs leading-relaxed text-fg-muted">
				Pick a filter from the strip {phone ? 'above' : 'below the image'}. Your original stays untouched until you
				apply and save.
			</p>
		{/if}

		<div class="flex items-center gap-2">
			<label for="filter-intensity" class="w-16 shrink-0 text-xs text-fg-muted">Intensity</label>
			<input
				id="filter-intensity"
				type="range"
				min="0"
				max="100"
				step="1"
				disabled={!active}
				value={state.filter.intensity}
				class="h-6 min-w-0 flex-1 accent-signal disabled:opacity-50"
				on:input={(event) => session.setFilterIntensity(intensityFrom(event))}
			/>
			<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{state.filter.intensity}%</span>
		</div>

		<div class="grid grid-cols-2 gap-2">
			<Button variant="secondary" size="sm" icon="sliders" disabled={!active} onclick={() => session.openFineTune()}>
				Fine-tune
			</Button>
			<Button
				variant="secondary"
				size="sm"
				icon="save"
				disabled={!active || lutBlocked}
				title={lutBlocked ? "LUT filters can't be copied yet" : undefined}
				onclick={onSaveFilter}
			>
				Save as filter
			</Button>
		</div>

		{#if lutBlocked}
			<p class="text-xs leading-relaxed text-fg-subtle" data-testid="lut-refusal">
				LUT filters can't be copied yet. Saved filters hold adjustment steps only.
			</p>
		{/if}

		<p class="text-xs leading-relaxed text-fg-subtle">
			Applies to <span class="font-semibold text-fg-muted">{layer?.name ?? 'the layer'}</span>{state.hasSelection
				? ' inside the selection'
				: ''}. Nothing changes until Apply.
		</p>

		<div class="flex items-center gap-2">
			<div class="flex-1">
				<Button variant="primary" size="sm" icon="check" class="w-full" disabled={!applicable} onclick={() => session.applyFilterToLayer()}>
					Apply
				</Button>
			</div>
			<Button variant="secondary" size="sm" disabled={!active} onclick={() => session.clearFilter()}>Reset</Button>
		</div>
	</div>
</div>
