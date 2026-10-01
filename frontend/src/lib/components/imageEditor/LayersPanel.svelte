<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { PAINT_ICONS } from './icons';
	import type { PaintSession, SessionSnapshot } from './session';
	import thumb from './thumb';
	import type { DocSnapshot } from './types';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let onAddImage: () => void;

	let opacityBefore: DocSnapshot | null = null;

	$: ordered = state.layers.map((layer, index) => ({ layer, index })).reverse();
	$: active = state.layers[state.activeIndex];
	$: opacityPercent = Math.round((active?.opacity ?? 1) * 100);

	const iconButton =
		'inline-flex items-center justify-center w-8 h-8 max-md:w-10 max-md:h-10 rounded border border-line-strong bg-surface-2 text-fg-muted transition-colors hover:border-line-hover hover:text-fg disabled:opacity-40 disabled:pointer-events-none';

	function beginOpacity() {
		opacityBefore = session.beginLayerOpacity(state.activeIndex);
	}

	function liveOpacity(event: Event) {
		if (!opacityBefore) beginOpacity();
		session.setLayerOpacity(state.activeIndex, Number((event.currentTarget as HTMLInputElement).value) / 100);
	}

	function commitOpacity() {
		session.finishLayerOpacity(opacityBefore);
		opacityBefore = null;
	}
</script>

<div class="flex flex-col gap-2 p-3">
	<div class="flex items-baseline justify-between">
		<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Layers</p>
		<span class="font-mono text-xs tabular-nums text-fg-muted">{state.layers.length}</span>
	</div>

	<ul class="flex flex-col gap-1" aria-label="Layers">
		{#each ordered as { layer, index } (layer.id)}
			<li>
				<div
					class="flex items-center gap-2 px-1.5 py-1 rounded border cursor-pointer min-h-10 {index === state.activeIndex
						? 'border-signal/60 bg-signal/10'
						: 'border-line bg-surface-2 hover:border-line-hover'}"
					role="button"
					tabindex="0"
					aria-current={index === state.activeIndex}
					aria-label="Select layer {layer.name}"
					on:click={() => session.setActiveLayer(index)}
					on:keydown={(event) => {
						if (event.key === 'Enter' || event.key === ' ') {
							event.preventDefault();
							session.setActiveLayer(index);
						}
					}}
				>
					<Tooltip text={layer.visible ? 'Hide layer' : 'Show layer'} position="left">
						<button
							type="button"
							class="inline-flex items-center justify-center w-7 h-7 rounded text-fg-muted hover:text-fg"
							aria-label={layer.visible ? 'Hide layer' : 'Show layer'}
							aria-pressed={layer.visible}
							on:click|stopPropagation={() => session.toggleLayerVisible(index)}
						>
							<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="1.8"
									d={layer.visible ? PAINT_ICONS.eye : PAINT_ICONS.eyeOff}
								/>
							</svg>
						</button>
					</Tooltip>
					<canvas
						width="32"
						height="32"
						class="w-8 h-8 shrink-0 rounded-sm bg-surface-3"
						use:thumb={{ layer, revision: state.revision }}
					></canvas>
					<span class="min-w-0 flex-1 truncate text-xs {layer.visible ? 'text-fg' : 'text-fg-disabled'}">
						{layer.name}
					</span>
				</div>
			</li>
		{/each}
	</ul>

	<div class="flex items-center gap-2">
		<label for="layer-opacity" class="w-16 shrink-0 text-xs text-fg-muted">Opacity</label>
		<input
			id="layer-opacity"
			type="range"
			min="0"
			max="100"
			value={opacityPercent}
			class="flex-1 min-w-0 h-6 accent-signal"
			on:input={liveOpacity}
			on:change={commitOpacity}
		/>
		<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{opacityPercent}%</span>
	</div>

	<div class="flex flex-wrap gap-1.5">
		<Tooltip text="Add layer" position="top">
			<button type="button" class={iconButton} aria-label="Add layer" on:click={() => session.addLayer()}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.plus} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Add an image as a layer" position="top">
			<button type="button" class={iconButton} aria-label="Add image as layer" on:click={onAddImage}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.addImage} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Duplicate layer" position="top">
			<button type="button" class={iconButton} aria-label="Duplicate layer" on:click={() => session.duplicateLayer()}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.duplicate} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Merge down" position="top">
			<button type="button" class={iconButton} aria-label="Merge down" disabled={state.activeIndex < 1} on:click={() => session.mergeDown()}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.merge} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Move layer up" position="top">
			<button type="button" class={iconButton} aria-label="Move layer up" disabled={state.activeIndex >= state.layers.length - 1} on:click={() => session.moveLayer('up')}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.up} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Move layer down" position="top">
			<button type="button" class={iconButton} aria-label="Move layer down" disabled={state.activeIndex < 1} on:click={() => session.moveLayer('down')}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.down} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Delete layer" position="top">
			<button type="button" class={iconButton} aria-label="Delete layer" disabled={state.layers.length < 2} on:click={() => session.deleteLayer()}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.trash} /></svg>
			</button>
		</Tooltip>
	</div>
</div>
