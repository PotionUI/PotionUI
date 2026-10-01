<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button } from '$lib/components/ui';
	import { PAINT_ICONS } from './icons';
	import { paintActions } from './registries';
	import type { PaintSession, SessionSnapshot } from './session';
	import type { PaintAction } from './types';
	import { onDestroy } from 'svelte';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let onOpenImage: () => void;

	let width = state.width;
	let height = state.height;
	let actions: PaintAction[] = paintActions.list();
	const stop = paintActions.subscribe(() => (actions = paintActions.list()));
	onDestroy(stop);

	$: if (state.width && state.height) {
		width = state.width;
		height = state.height;
	}

	const iconButton =
		'inline-flex items-center justify-center w-8 h-8 max-md:w-10 max-md:h-10 rounded border border-line-strong bg-surface-2 text-fg-muted transition-colors hover:border-line-hover hover:text-fg';
</script>

<div class="flex flex-col gap-3 p-3">
	<div class="flex items-baseline justify-between">
		<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Canvas</p>
		<span class="font-mono text-xs tabular-nums text-fg-muted">{state.width} × {state.height}</span>
	</div>

	<div class="flex flex-wrap gap-1.5">
		<Tooltip text="Flip horizontally" position="top">
			<button type="button" class={iconButton} aria-label="Flip horizontally" on:click={() => session.flip('horizontal')}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.flipH} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Flip vertically" position="top">
			<button type="button" class={iconButton} aria-label="Flip vertically" on:click={() => session.flip('vertical')}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.flipV} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Rotate left" position="top">
			<button type="button" class={iconButton} aria-label="Rotate left" on:click={() => session.rotate('ccw')}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.rotateLeft} /></svg>
			</button>
		</Tooltip>
		<Tooltip text="Rotate right" position="top">
			<button type="button" class={iconButton} aria-label="Rotate right" on:click={() => session.rotate('cw')}>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.rotateRight} /></svg>
			</button>
		</Tooltip>
	</div>

	<div class="flex items-center gap-1.5">
		<input
			type="number"
			min="16"
			max="4096"
			bind:value={width}
			aria-label="Canvas width"
			class="input flex-1 min-w-0 font-mono tabular-nums"
		/>
		<span class="text-fg-subtle">×</span>
		<input
			type="number"
			min="16"
			max="4096"
			bind:value={height}
			aria-label="Canvas height"
			class="input flex-1 min-w-0 font-mono tabular-nums"
		/>
	</div>
	<div class="flex items-center gap-2">
		<Button variant="secondary" size="sm" class="flex-1" onclick={() => session.resizeCanvas('scale', width, height)}>
			Scale
		</Button>
		<Button variant="secondary" size="sm" class="flex-1" onclick={() => session.resizeCanvas('extend', width, height)}>
			Extend
		</Button>
	</div>
	<p class="text-xs leading-relaxed text-fg-subtle">
		Scale resizes the picture with the canvas. Extend adds or removes space around it. Both change the
		image size.
	</p>

	<div class="md:hidden">
		<Button variant="secondary" size="sm" icon="folder" class="w-full" onclick={onOpenImage}>
			Open another image
		</Button>
	</div>

	{#if actions.length > 0}
		<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Actions</p>
		<div class="flex flex-wrap gap-1.5">
			{#each actions as action (action.id)}
				<Button variant="secondary" size="sm" onclick={() => action.run(session)}>{action.label}</Button>
			{/each}
		</div>
	{/if}
</div>
