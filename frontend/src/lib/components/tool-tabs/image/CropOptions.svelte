<script lang="ts" module>
	export const IMAGE_ASPECTS = [
		{ key: 'free', label: 'Free', ratio: null },
		{ key: '1:1', label: '1:1', ratio: 1 },
		{ key: '4:3', label: '4:3', ratio: 4 / 3 },
		{ key: '3:2', label: '3:2', ratio: 3 / 2 },
		{ key: '16:9', label: '16:9', ratio: 16 / 9 },
		{ key: '9:16', label: '9:16', ratio: 9 / 16 }
	] as const;

	export const OUTPUT_SIDES = [768, 1024, 1536, 2048];
	export const HINT = 'Drag inside the frame to move it, corners to resize. Arrow keys nudge, Enter applies.';

	export function aspectLabel(key: string): string {
		return IMAGE_ASPECTS.find((aspect) => aspect.key === key)?.label ?? 'Free';
	}
</script>

<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';
	import EntryForm from '$lib/components/tool-tabs/frame/EntryForm.svelte';
	import { ORIENTATION, rectInPixels, withAspect, withOrientation, type OrientationKey } from '$lib/tool-tabs/image/cropActions';
	import { cropAspectFields, cropFromLongest, cropFromRect, cropLongestFields, cropRectFields, cropValues } from '$lib/tool-tabs/image/optionSchemas';
	import { planDisplaySize, planOutputSize } from '$lib/media/imageEditPlan';
	import type { PixelSize } from '$lib/tool-tabs/image/payload';
	import type { CropParams } from '$lib/tool-tabs/image/steps';

	let {
		params,
		input,
		phone = false,
		aspects = IMAGE_ASPECTS,
		outputSides = OUTPUT_SIDES,
		sideLimit = 3,
		hint = HINT,
		onchange
	}: {
		params: CropParams;
		input: PixelSize;
		phone?: boolean;
		aspects?: readonly { key: string; label: string; ratio: number | null }[];
		outputSides?: readonly number[];
		sideLimit?: number;
		hint?: string | null;
		onchange: (next: CropParams) => void;
	} = $props();

	let frame = $derived(planDisplaySize(params.plan, input));
	let pixels = $derived(rectInPixels(params, frame));
	let output = $derived(planOutputSize(params.plan, input));
	let cropped = $derived(planOutputSize({ ...params.plan, targetLongestSide: null }, input));
	let sides = $derived(outputSides.filter((side) => side < Math.max(cropped.width, cropped.height)));
	let values = $derived(cropValues(params, pixels));
	let aspectFields = $derived(cropAspectFields(aspects));
	let longestFields = $derived(cropLongestFields(sides.slice(0, sideLimit)));
	const rectFields = cropRectFields();

	const chip =
		'h-8 rounded border font-mono text-sm tabular-nums transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal';
	const on = 'border-signal/60 bg-signal/10 text-signal';
	const off = 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';

	function chooseAspect(key: string, ratio: number | null) {
		onchange(withAspect(params, key, ratio, frame));
	}

	function orient(key: OrientationKey) {
		onchange(withOrientation(params, key));
	}

	function write(next: Record<string, unknown>, info: { commit: boolean; name?: string }) {
		const name = info.name;
		if (name === 'aspect') {
			if (info.commit) return;
			const key = String(next.aspect);
			chooseAspect(key, aspects.find((aspect) => aspect.key === key)?.ratio ?? null);
			return;
		}
		if (name === 'longest') {
			if (!info.commit) onchange(cropFromLongest(params, next.longest));
			return;
		}
		if (!info.commit || !name) return;
		const rect = cropFromRect(params, frame, name, next[name]);
		if (rect) onchange(rect);
	}
</script>

{#if phone}
	<div class="flex gap-1.5 overflow-x-auto" role="group" aria-label="Aspect" data-crop-aspects>
		{#each aspects as aspect (aspect.key)}
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
	<div data-crop-aspects>
		<EntryForm fields={aspectFields} {values} label="Aspect" onValues={write} />
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
	<div data-crop-fields>
		<EntryForm fields={rectFields} {values} label="Crop rectangle" onValues={write} />
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
		<EntryForm fields={longestFields} {values} label="Longest side" onValues={write} />
	{/if}
	{#if hint}<p class="text-sm text-fg-muted">{hint}</p>{/if}
{/if}
