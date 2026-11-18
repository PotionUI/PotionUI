<script lang="ts">
	import EntryForm from '$lib/components/tool-tabs/frame/EntryForm.svelte';
	import { Button, IconButton, SegmentedControl } from '$lib/components/ui';
	import { overlays } from '$lib/editors/overlays';
	import { MASK_BRUSH } from '$lib/tool-tabs/image/mask';
	import { maskBrushFields, maskMarkingFields, maskValues } from '$lib/tool-tabs/image/optionSchemas';
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
		erase = $bindable<boolean>(false),
		what = null,
		compact = false,
		onclear
	}: {
		brush?: number;
		erase?: boolean;
		what?: string | null;
		compact?: boolean;
		onclear?: () => void;
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

	let values = $derived(maskValues(active, mode, brush));
	let markingFields = $derived(maskMarkingFields(toolItems, active, modeItems));
	let brushFields = $derived(maskBrushFields(active));

	function pickMode(id: string) {
		erase = active?.modes?.find((entry) => entry.id === id)?.subtract ?? false;
	}

	function write(next: Record<string, unknown>, info: { commit: boolean; name?: string }) {
		if (info.commit) return;
		if (info.name === 'tool') chooseOverlay(String(next.tool));
		else if (info.name === 'mode') pickMode(String(next.mode));
		else if (info.name === 'brush') brush = Number(next.brush);
	}
</script>

{#if compact}
	<div class="flex flex-none items-center gap-2.5" data-mask-compact>
		{#if toolItems.length > 1}
			<SegmentedControl variant="toggle" ariaLabel="Marking tool" items={toolItems} selected={active?.id ?? ''} onSelect={chooseOverlay} />
		{/if}
		{#if modeItems.length}
			{#if toolItems.length > 1}<span class="h-6 w-px bg-line" aria-hidden="true"></span>{/if}
			<SegmentedControl variant="toggle" ariaLabel={active?.modesLabel ?? 'Marking mode'} items={modeItems} selected={mode?.id ?? ''} onSelect={pickMode} />
		{/if}
		<span class="flex-1"></span>
		{#if onclear}<IconButton icon="trash" label="Clear area" size="sm" disabled={!$maskArea} onclick={onclear} data-clear-area />{/if}
	</div>
	{#if brushFields.length}
		<EntryForm fields={brushFields} {values} label="Brush" onValues={write} />
	{/if}
{:else}
<div class="flex flex-col gap-2">
	<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Mark the area</p>
	{#if markingFields.length}
		<EntryForm fields={markingFields} {values} label="Marking" onValues={write} />
	{/if}
</div>
{#if active?.hint}
	<p class="text-sm text-fg-muted">{what ? active.hint.replace('part you want to change', what) : active.hint}</p>
{/if}
<div class="flex items-center justify-between gap-2 text-sm" data-mask-area-readout>
	<span class="text-fg-muted">Marked area</span>
	<span class="flex items-center gap-2">
		<span class="font-mono tabular-nums text-fg">{$maskArea ? `${$maskArea.width}×${$maskArea.height}` : 'None'}</span>
		{#if onclear}<Button variant="ghost" size="sm" disabled={!$maskArea} onclick={onclear} attrs={{ 'data-clear-area': '' }}>Clear area</Button>{/if}
	</span>
</div>
{/if}
