<script lang="ts">
	import { IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { planDisplaySize, planOutputSize } from '$lib/media/editors/imageEditPlan';
	import { ORIENTATION, withAspect, withOrientation, type OrientationKey } from '$lib/tool-tabs/image/cropActions';
	import type { PixelSize } from '$lib/tool-tabs/image/payload';
	import type { CropParams } from '$lib/tool-tabs/image/steps';
	import { IMAGE_ASPECTS } from './CropOptions.svelte';

	let {
		params,
		input,
		phone = false,
		onchange
	}: {
		params: CropParams;
		input: PixelSize;
		phone?: boolean;
		onchange: (next: CropParams) => void;
	} = $props();

	let frame = $derived(planDisplaySize(params.plan, input));
	let output = $derived(planOutputSize(params.plan, input));

	const chip =
		'h-7 flex-none rounded px-2.5 font-mono text-sm tabular-nums transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal';
</script>

<div class="inline-flex flex-none items-center gap-0.5 rounded border border-line bg-surface-2 p-0.5" role="group" aria-label="Aspect" data-crop-aspects>
	{#each IMAGE_ASPECTS as aspect (aspect.key)}
		<button
			type="button"
			class="{chip} {params.aspect === aspect.key ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3 hover:text-fg'}"
			aria-pressed={params.aspect === aspect.key}
			onclick={() => onchange(withAspect(params, aspect.key, aspect.ratio, frame))}>{aspect.label}</button
		>
	{/each}
</div>
{#if !phone}
	<span class="h-5 w-px flex-none bg-line-strong" aria-hidden="true"></span>
{/if}
<div class="flex flex-none items-center gap-0.5" role="group" aria-label="Orientation">
	{#each ORIENTATION as item (item.key)}
		<Tooltip text={item.label} position="bottom">
			<IconButton icon={item.icon} label={item.label} size="sm" onclick={() => onchange(withOrientation(params, item.key as OrientationKey))} data-crop-orient={item.key} />
		</Tooltip>
	{/each}
</div>
<span class="ml-auto flex-none pl-2 font-mono text-sm tabular-nums text-success" data-crop-output>{output.width}×{output.height}</span>
