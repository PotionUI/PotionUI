<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';
	import { resizeOutput, type PixelSize } from '$lib/tool-tabs/image/payload';
	import type { ResizeParams } from '$lib/tool-tabs/image/steps';

	let {
		params,
		input,
		phone = false,
		onchange
	}: {
		params: ResizeParams;
		input: PixelSize;
		phone?: boolean;
		onchange: (next: ResizeParams) => void;
	} = $props();

	const SCALES = [50, 75, 150, 200];
	let output = $derived(resizeOutput(params, input));

	const field =
		'h-8 w-full rounded border border-line-strong bg-surface-2 px-2 font-mono text-sm tabular-nums text-fg focus:border-signal/60 focus:outline-none';
	const chip = 'h-8 rounded border font-mono text-sm tabular-nums transition-colors border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';

	function clampSide(value: number): number {
		return Math.max(1, Math.min(16384, Math.round(value)));
	}

	function setWidth(raw: string) {
		const width = clampSide(Number(raw) || 1);
		onchange({ ...params, width, height: params.lock ? Math.max(1, Math.round((width * input.height) / Math.max(1, input.width))) : params.height });
	}

	function setHeight(raw: string) {
		const height = clampSide(Number(raw) || 1);
		onchange({ ...params, height, width: params.lock ? Math.max(1, Math.round((height * input.width) / Math.max(1, input.height))) : params.width });
	}

	function scale(percent: number) {
		const width = clampSide((input.width * percent) / 100);
		onchange({ ...params, lock: true, width, height: Math.max(1, Math.round((width * input.height) / Math.max(1, input.width))) });
	}
</script>

<div class="flex items-end gap-2">
	<label class="flex min-w-0 flex-1 flex-col gap-1 text-sm text-fg-muted">
		Width
		<input class={field} type="number" min="1" value={output.width} onchange={(event) => setWidth(event.currentTarget.value)} data-resize-width />
	</label>
	<button
		type="button"
		class="mb-0.5 grid h-7 w-7 flex-none place-items-center rounded {params.lock ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3'}"
		aria-pressed={params.lock}
		aria-label="Keep aspect ratio"
		use:tooltip={'Keep aspect ratio'}
		onclick={() => onchange({ ...params, lock: !params.lock })}
	>
		<Icon name="lock" className="h-3.5 w-3.5" />
	</button>
	<label class="flex min-w-0 flex-1 flex-col gap-1 text-sm text-fg-muted">
		Height
		<input class={field} type="number" min="1" value={output.height} onchange={(event) => setHeight(event.currentTarget.value)} data-resize-height />
	</label>
</div>
<div class="grid grid-cols-4 gap-1.5" role="group" aria-label="Scale">
	{#each SCALES as percent (percent)}
		<button type="button" class={chip} onclick={() => scale(percent)}>{percent}%</button>
	{/each}
</div>
{#if !phone}
	<div class="flex flex-col gap-1.5 text-sm">
		<div class="flex items-center justify-between">
			<span class="text-fg-muted">Source</span>
			<span class="font-mono tabular-nums text-fg">{input.width}×{input.height}</span>
		</div>
		<div class="flex items-center justify-between">
			<span class="text-fg-muted">Output</span>
			<span class="font-mono tabular-nums text-success">{output.width}×{output.height}</span>
		</div>
	</div>
	<p class="text-sm text-fg-muted">Resampled with Lanczos. Unlock to set both sides.</p>
{/if}
