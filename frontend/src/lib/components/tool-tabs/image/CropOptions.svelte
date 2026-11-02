<script lang="ts" module>
	export const IMAGE_ASPECTS = [
		{ key: 'free', label: 'Free', ratio: null },
		{ key: '1:1', label: '1:1', ratio: 1 },
		{ key: '4:3', label: '4:3', ratio: 4 / 3 },
		{ key: '3:2', label: '3:2', ratio: 3 / 2 },
		{ key: '16:9', label: '16:9', ratio: 16 / 9 },
		{ key: '9:16', label: '9:16', ratio: 9 / 16 }
	] as const;

	export function aspectLabel(key: string): string {
		return IMAGE_ASPECTS.find((aspect) => aspect.key === key)?.label ?? 'Free';
	}
</script>

<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';
	import { fitAspect, mirrorCropRect, rotateCropRect, turn } from '$lib/media/editors/cropGeometry';
	import { planDisplaySize, planOutputSize } from '$lib/media/editors/imageEditPlan';
	import type { PixelSize } from '$lib/tool-tabs/image/payload';
	import type { CropParams } from '$lib/tool-tabs/image/steps';

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

	const OUTPUT_SIDES = [768, 1024, 1536, 2048];
	const ORIENTATION = [
		{ key: 'rotate-left', label: 'Rotate left', icon: 'editor-rotate-left' },
		{ key: 'rotate-right', label: 'Rotate right', icon: 'editor-rotate-right' },
		{ key: 'flip-h', label: 'Flip horizontal', icon: 'flip' },
		{ key: 'flip-v', label: 'Flip vertical', icon: 'editor-flip-v' }
	] as const;

	let frame = $derived(planDisplaySize(params.plan, input));
	let output = $derived(planOutputSize(params.plan, input));
	let cropped = $derived(planOutputSize({ ...params.plan, targetLongestSide: null }, input));
	let sides = $derived(OUTPUT_SIDES.filter((side) => side < Math.max(cropped.width, cropped.height)));

	const chip =
		'h-8 rounded border font-mono text-sm tabular-nums transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal';
	const on = 'border-signal/60 bg-signal/10 text-signal';
	const off = 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';

	function chooseAspect(key: string, ratio: number | null) {
		const aspect = frame.height > 0 ? frame.width / frame.height : 1;
		onchange({ aspect: key, plan: ratio ? { ...params.plan, rect: fitAspect(params.plan.rect, ratio, aspect) } : params.plan });
	}

	function orient(key: (typeof ORIENTATION)[number]['key']) {
		const plan = params.plan;
		if (key === 'rotate-left' || key === 'rotate-right') {
			const quarters = key === 'rotate-right' ? 1 : -1;
			onchange({ ...params, plan: { ...plan, rotation: turn(plan.rotation, quarters), rect: rotateCropRect(plan.rect, quarters) } });
			return;
		}
		const axis = key === 'flip-h' ? 'horizontal' : 'vertical';
		onchange({
			...params,
			plan: {
				...plan,
				flipHorizontal: axis === 'horizontal' ? !plan.flipHorizontal : plan.flipHorizontal,
				flipVertical: axis === 'vertical' ? !plan.flipVertical : plan.flipVertical,
				rect: mirrorCropRect(plan.rect, axis)
			}
		});
	}

	function longest(side: number | null) {
		onchange({ ...params, plan: { ...params.plan, targetLongestSide: side } });
	}
</script>

{#if phone}
	<div class="flex gap-1.5 overflow-x-auto" role="group" aria-label="Aspect" data-crop-aspects>
		{#each IMAGE_ASPECTS as aspect (aspect.key)}
			<button
				type="button"
				class="{chip} flex-none px-3 {params.aspect === aspect.key ? on : off}"
				aria-pressed={params.aspect === aspect.key}
				onclick={() => chooseAspect(aspect.key, aspect.ratio)}>{aspect.label}</button
			>
		{/each}
		{#each ORIENTATION as item (item.key)}
			<button type="button" class="{chip} grid w-10 flex-none place-items-center {off}" aria-label={item.label} onclick={() => orient(item.key)}>
				<Icon name={item.icon} className="h-4 w-4" />
			</button>
		{/each}
	</div>
	<div class="flex items-center justify-between text-sm">
		<span class="text-fg-subtle">Output</span>
		<span class="font-mono tabular-nums text-success" data-crop-output>{output.width}×{output.height}</span>
	</div>
{:else}
	<div>
		<p class="mb-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Aspect</p>
		<div class="grid grid-cols-3 gap-1.5" role="group" aria-label="Aspect" data-crop-aspects>
			{#each IMAGE_ASPECTS as aspect (aspect.key)}
				<button
					type="button"
					class="{chip} {params.aspect === aspect.key ? on : off}"
					aria-pressed={params.aspect === aspect.key}
					onclick={() => chooseAspect(aspect.key, aspect.ratio)}>{aspect.label}</button
				>
			{/each}
		</div>
	</div>
	<div>
		<p class="mb-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Orientation</p>
		<div class="grid grid-cols-4 gap-1.5" role="group" aria-label="Orientation">
			{#each ORIENTATION as item (item.key)}
				<button
					type="button"
					class="{chip} grid place-items-center {off}"
					aria-label={item.label}
					use:tooltip={item.label}
					onclick={() => orient(item.key)}
					data-crop-orient={item.key}
				>
					<Icon name={item.icon} className="h-4 w-4" />
				</button>
			{/each}
		</div>
	</div>
	<div class="h-px bg-line"></div>
	<div class="flex flex-col gap-1.5 text-sm">
		<div class="flex items-center justify-between">
			<span class="text-fg-muted">Source</span>
			<span class="font-mono tabular-nums text-fg">{input.width}×{input.height}</span>
		</div>
		<div class="flex items-center justify-between">
			<span class="text-fg-muted">Output</span>
			<span class="font-mono tabular-nums text-success" data-crop-output>{output.width}×{output.height}</span>
		</div>
	</div>
	{#if sides.length}
		<div>
			<p class="mb-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Longest side</p>
			<div class="grid grid-cols-4 gap-1.5" role="group" aria-label="Longest side">
				<button type="button" class="{chip} {params.plan.targetLongestSide === null ? on : off}" aria-pressed={params.plan.targetLongestSide === null} onclick={() => longest(null)}>FULL</button>
				{#each sides.slice(0, 3) as side (side)}
					<button type="button" class="{chip} {params.plan.targetLongestSide === side ? on : off}" aria-pressed={params.plan.targetLongestSide === side} onclick={() => longest(side)}>{side}</button>
				{/each}
			</div>
		</div>
	{/if}
	<p class="text-sm text-fg-muted">Drag inside the frame to move it, corners to resize. Arrow keys nudge, Enter applies.</p>
{/if}
