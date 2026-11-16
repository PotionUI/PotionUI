<script lang="ts">
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import { SegmentedControl } from '$lib/components/ui';
	import { overlays } from '$lib/editors/overlays';
	import { MASK_BRUSH } from '$lib/tool-tabs/image/mask';
	import { ensureCoreMaskOverlays } from '$lib/tool-tabs/image/coreMaskOverlays';
	import {
		activeOverlay,
		chooseOverlay,
		maskArea,
		maskToolEntries,
		maskToolState,
		modeFor,
		overlayLabel
	} from '$lib/tool-tabs/image/maskOverlays';

	let {
		brush = $bindable<number>(MASK_BRUSH.default),
		erase = $bindable<boolean>(false)
	}: {
		brush?: number;
		erase?: boolean;
	} = $props();

	ensureCoreMaskOverlays();

	const revision = overlays.revision;

	let entries = $derived.by(() => {
		void $revision;
		return maskToolEntries();
	});
	let active = $derived(activeOverlay(entries, $maskToolState));
	let mode = $derived(modeFor(active, erase));
	let toolItems = $derived(entries.map((entry) => ({ id: entry.id, label: overlayLabel(entry) })));
	let modeItems = $derived((active?.modes ?? []).map(({ id, label }) => ({ id, label })));

	function pickMode(id: string) {
		erase = active?.modes?.find((entry) => entry.id === id)?.subtract ?? false;
	}
</script>

<div class="flex flex-col gap-2">
	<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Mark the area</p>
	{#if toolItems.length > 1}
		<SegmentedControl variant="toggle" fullWidth ariaLabel="Marking tool" items={toolItems} selected={active?.id ?? ''} onSelect={chooseOverlay} />
	{/if}
	{#if modeItems.length}
		<SegmentedControl variant="toggle" fullWidth ariaLabel={active?.modesLabel ?? 'Marking mode'} items={modeItems} selected={mode?.id ?? ''} onSelect={pickMode} />
	{/if}
</div>
{#if active?.size}
	<SliderField
		name="brush"
		config={{ title: active.size.label, min: active.size.min, max: active.size.max, step: active.size.step, default: active.size.default, unit: active.size.unit }}
		value={brush}
		onChange={(_name, value) => (brush = Number(value))}
	/>
{/if}
{#if active?.hint}
	<p class="text-sm text-fg-muted">{active.hint}</p>
{/if}
<div class="flex items-baseline justify-between gap-2 text-sm" data-mask-area-readout>
	<span class="text-fg-muted">Marked area</span>
	<span class="font-mono tabular-nums text-fg">{$maskArea ? `${$maskArea.width}×${$maskArea.height}` : 'None'}</span>
</div>
