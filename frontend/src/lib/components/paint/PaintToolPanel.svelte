<script lang="ts">
	import { Button } from '$lib/components/ui';
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import AdjustPanel from '$lib/components/paint/AdjustPanel.svelte';
	import ColourControl from '$lib/components/paint/ColourControl.svelte';
	import CropPanel from '$lib/components/paint/CropPanel.svelte';
	import FiltersPanel from '$lib/components/paint/FiltersPanel.svelte';
	import TransformPanel from '$lib/components/paint/TransformPanel.svelte';
	import { DEFAULT_SIZE } from '$lib/paint/palette';
	import { selectionTarget } from '$lib/paint/raster/layerContent';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';

	let {
		session,
		state: snapshot,
		phone = false,
		onSaveFilter = () => {}
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		phone?: boolean;
		onSaveFilter?: () => void;
	} = $props();

	let tool = $derived(snapshot.tools.find((candidate) => candidate.id === snapshot.toolId));
	let panel = $derived(tool?.panel ?? 'options');
	let options = $derived(tool?.options ?? []);
	let target = $derived(selectionTarget(snapshot.activeLayerName, snapshot.activeLayerEmpty));
</script>

<div class="flex flex-col gap-3" data-paint-tool-panel={snapshot.toolId}>
	{#if panel === 'adjust'}
		<AdjustPanel {session} state={snapshot} {onSaveFilter} />
	{:else if panel === 'filters'}
		<FiltersPanel {session} state={snapshot} {phone} {onSaveFilter} />
	{:else if panel === 'crop'}
		<CropPanel {session} state={snapshot} />
	{:else if panel === 'transform'}
		<TransformPanel {session} state={snapshot} />
	{:else if panel === 'selection'}
		<div class="flex flex-col gap-2.5" data-paint-selection>
			<div class="flex items-baseline justify-between gap-2">
				<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Selection</span>
				<span class="font-mono text-sm tabular-nums text-fg-muted" data-selection-size>
					{#if snapshot.selectionSize}{snapshot.selectionSize.width} × {snapshot.selectionSize.height}{:else}none{/if}
				</span>
			</div>
			{#if target.label}
				<p class="text-sm text-fg-muted" data-selection-layer>{target.label}</p>
			{/if}
			<div class="grid grid-cols-3 gap-1.5">
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection || !!target.emptyReason} onclick={() => session.cutSelection()}>Cut</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection} onclick={() => session.copySelection()}>Copy</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasClipboard} onclick={() => session.pasteClipboard()}>Paste</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection || !!target.emptyReason} onclick={() => session.deleteSelection()}>Delete</Button>
				<Button variant="secondary" size="sm" onclick={() => session.selectAll()}>Select all</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection} onclick={() => session.deselect()}>Deselect</Button>
			</div>
			{#if snapshot.hasSelection && target.emptyReason}
				<p class="text-sm text-fg-subtle" data-selection-empty>{target.emptyReason}</p>
			{/if}
		</div>
	{:else}
		{#if options.includes('size')}
			<div data-field-name="size">
				<SliderField name="paint-size" config={{ title: 'Size', min: 1, max: 200, default: DEFAULT_SIZE, unit: ' px', reset: true, legend: false }} value={snapshot.settings.size} onChange={(_name, value) => session.setSetting('size', Number(value))} />
			</div>
		{/if}
		{#if options.includes('opacity')}
			<div data-field-name="opacity">
				<SliderField name="paint-opacity" config={{ title: 'Opacity', min: 1, max: 100, default: 100, unit: '%', reset: true, legend: false }} value={snapshot.settings.opacity} onChange={(_name, value) => session.setSetting('opacity', Number(value))} />
			</div>
		{/if}
		{#if options.includes('tolerance')}
			<div data-field-name="tolerance">
				<SliderField name="paint-tolerance" config={{ title: 'Tolerance', min: 0, max: 100, default: 32, reset: true, legend: false }} value={snapshot.settings.tolerance} onChange={(_name, value) => session.setSetting('tolerance', Number(value))} />
			</div>
		{/if}
		{#if options.includes('color')}
			<div class="flex flex-col gap-1.5">
				<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Colour</span>
				<ColourControl color={snapshot.settings.color} onchange={(hex) => session.setColor(hex)} />
			</div>
		{/if}
	{/if}
	{#if tool?.hint && (panel === 'options' || panel === 'selection')}
		<p class="text-sm text-fg-subtle" data-paint-hint>{tool.hint}</p>
	{/if}
</div>
