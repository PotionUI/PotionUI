<script lang="ts" module>
	export const SCALES = [50, 75, 150, 200];
</script>

<script lang="ts">
	import EntryForm from '$lib/components/tool-tabs/frame/EntryForm.svelte';
	import { resizeOutput, type PixelSize } from '$lib/tool-tabs/image/payload';
	import { resizeFields, resizeFromScale, resizeFromSide, resizeValues } from '$lib/tool-tabs/image/optionSchemas';
	import type { ResizeParams } from '$lib/tool-tabs/image/steps';

	let {
		params,
		input,
		phone = false,
		scales = SCALES,
		onchange
	}: {
		params: ResizeParams;
		input: PixelSize;
		phone?: boolean;
		scales?: readonly number[];
		onchange: (next: ResizeParams) => void;
	} = $props();

	let output = $derived(resizeOutput(params, input));
	let values = $derived(resizeValues(params, input));
	const fields = resizeFields();

	const chip = 'h-8 rounded border font-mono text-sm tabular-nums transition-colors border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';

	function write(next: Record<string, unknown>, info: { commit: boolean; name?: string }) {
		if (info.name === 'lock') {
			if (!info.commit) onchange({ ...params, lock: next.lock === true });
			return;
		}
		if (!info.commit || (info.name !== 'width' && info.name !== 'height')) return;
		onchange(resizeFromSide(params, input, info.name, next[info.name]));
	}
</script>

<EntryForm {fields} {values} label="Resize" onValues={write} />
{#if scales.length}
<div class="grid gap-1.5" style="grid-template-columns: repeat({scales.length}, minmax(0, 1fr))" role="group" aria-label="Scale">
	{#each scales as percent (percent)}
		<button type="button" class={chip} onclick={() => onchange(resizeFromScale(params, input, percent))}>{percent}%</button>
	{/each}
</div>
{/if}
{#if !phone}
	<div class="flex flex-col gap-1.5 text-sm">
		<div class="flex items-center justify-between">
			<span class="text-fg-muted">Source</span>
			<span class="font-mono tabular-nums text-fg">{input.width}×{input.height}</span>
		</div>
		<div class="flex items-center justify-between">
			<span class="text-fg-muted">Output</span>
			<span class="font-mono tabular-nums text-success" data-testid="resize-result">{output.width}×{output.height}</span>
		</div>
	</div>
	<p class="text-sm text-fg-muted">Resampled with Lanczos. Unlock to set both sides.</p>
{/if}
