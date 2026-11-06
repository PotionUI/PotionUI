<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton } from '$lib/components/ui';
	import PropertySlider from '$lib/components/tool-tabs/frame/PropertySlider.svelte';
	import type { EffectTool } from '$lib/effects/catalog';
	import { CROP_ASPECTS, mirrorCropRect, rotateCropRect, turn } from '$lib/media/editors/cropGeometry';
	import { fixed2, readCrop, type RailToolId, type VideoOp, type VideoParams } from '$lib/tool-tabs/video/videoSteps';
	import TimeField from './TimeField.svelte';

	let {
		tool,
		title,
		icon,
		op,
		params,
		duration,
		trimIn,
		trimOut,
		outputLength,
		aspect = 'free',
		size,
		effect = null,
		phone = false,
		onparams,
		onaspect,
		ontrim
	}: {
		tool: RailToolId;
		title: string;
		icon: string;
		op: VideoOp | null;
		params: VideoParams;
		duration: number;
		trimIn: number;
		trimOut: number;
		outputLength: number;
		aspect?: string;
		size: { width: number; height: number };
		effect?: EffectTool | null;
		phone?: boolean;
		onparams: (op: VideoOp, params: VideoParams) => void;
		onaspect: (key: string) => void;
		ontrim: (which: 'in' | 'out', seconds: number) => void;
	} = $props();

	let crop = $derived(tool === 'crop' ? readCrop(params) : null);
	let strength = $derived(effect?.params.find((param) => (param.type === 'float' || param.type === 'integer') && typeof param.min === 'number' && typeof param.max === 'number') ?? null);

	function rotate(quarters: number) {
		if (!crop) return;
		onparams('crop', { ...params, rect: rotateCropRect(crop.rect, quarters), rotation: turn(crop.rotation, quarters) });
	}

	function flip(axis: 'horizontal' | 'vertical') {
		if (!crop) return;
		const key = axis === 'horizontal' ? 'flipH' : 'flipV';
		onparams('crop', { ...params, rect: mirrorCropRect(crop.rect, axis), [key]: !crop[key] });
	}

	const chip =
		'h-7 flex-none rounded px-2.5 font-mono text-sm tabular-nums transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal';
</script>

{#if !phone}
	<span class="inline-flex flex-none items-center gap-2 text-base font-semibold text-fg" data-bar-title>
		<Icon name={icon} className="h-3.5 w-3.5 flex-none text-fg-muted" />{title}
	</span>
{/if}
{#if tool === 'trim'}
	<TimeField which="in" value={trimIn} max={duration} compact={!phone} oncommit={ontrim} />
	<TimeField which="out" value={trimOut} max={duration} compact={!phone} oncommit={ontrim} />
	<span class="flex-none font-mono text-sm uppercase tabular-nums text-fg-subtle">Length <span class="normal-case text-fg" data-bar-length>{fixed2(outputLength)} s</span></span>
{:else if tool === 'crop' && crop}
	<div class="inline-flex flex-none items-center gap-0.5 rounded border border-line bg-surface-2 p-0.5" role="group" aria-label="Aspect" data-bar-aspects>
		{#each CROP_ASPECTS as entry (entry.key)}
			<button
				type="button"
				class="{chip} {aspect === entry.key ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3 hover:text-fg'}"
				aria-pressed={aspect === entry.key}
				onclick={() => onaspect(entry.key)}>{entry.label}</button
			>
		{/each}
	</div>
	<div class="flex flex-none items-center gap-0.5" role="group" aria-label="Orientation">
		<Tooltip text="Rotate left" position="bottom"><IconButton icon="editor-rotate-left" label="Rotate left" size="sm" onclick={() => rotate(-1)} data-bar-orient="rotate-left" /></Tooltip>
		<Tooltip text="Rotate right" position="bottom"><IconButton icon="editor-rotate-right" label="Rotate right" size="sm" onclick={() => rotate(1)} data-bar-orient="rotate-right" /></Tooltip>
		<Tooltip text="Flip horizontally" position="bottom"><IconButton icon="flip" label="Flip horizontally" size="sm" active={crop.flipH} onclick={() => flip('horizontal')} data-bar-orient="flip-h" /></Tooltip>
		<Tooltip text="Flip vertically" position="bottom"><IconButton icon="editor-flip-v" label="Flip vertically" size="sm" active={crop.flipV} onclick={() => flip('vertical')} data-bar-orient="flip-v" /></Tooltip>
	</div>
	<span class="ml-auto flex-none font-mono text-sm tabular-nums text-success" data-bar-size>{size.width}×{size.height}</span>
{:else if tool === 'effects' && effect}
	<span class="inline-flex h-6 flex-none items-center rounded bg-signal/10 px-2 font-mono text-sm text-signal" data-bar-effect>{effect.label}</span>
	{#if strength && op}
		{@const min = strength.min ?? 0}
		{@const max = strength.max ?? 1}
		<div class="flex w-64 min-w-40 max-w-80 flex-1 items-center">
			<PropertySlider
				inline
				label={strength.label ?? strength.name}
				value={Number(params[strength.name] ?? strength.default ?? min)}
				{min}
				{max}
				step={strength.step ?? (strength.type === 'integer' ? 1 : (max - min) / 100)}
				defaultValue={Number(strength.default ?? min)}
				onchange={(value) => onparams(op, { ...params, [strength.name]: value })}
			/>
		</div>
	{/if}
{:else if tool === 'speed'}
	<span class="ml-auto flex-none font-mono text-sm tabular-nums text-fg-muted" data-bar-length>{fixed2(outputLength)} s</span>
{/if}
