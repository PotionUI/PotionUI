<script lang="ts">
	import { Button } from '$lib/components/ui';
	import PropertySlider from '$lib/components/tool-tabs/frame/PropertySlider.svelte';
	import AdjustPanel from '$lib/components/paint/AdjustPanel.svelte';
	import ColourControl from '$lib/components/paint/ColourControl.svelte';
	import CropPanel from '$lib/components/paint/CropPanel.svelte';
	import FiltersPanel from '$lib/components/paint/FiltersPanel.svelte';
	import TransformPanel from '$lib/components/paint/TransformPanel.svelte';
	import { DEFAULT_SIZE } from '$lib/paint/palette';
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
			<div class="grid grid-cols-3 gap-1.5">
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection} onclick={() => session.cutSelection()}>Cut</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection} onclick={() => session.copySelection()}>Copy</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasClipboard} onclick={() => session.pasteClipboard()}>Paste</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection} onclick={() => session.deleteSelection()}>Delete</Button>
				<Button variant="secondary" size="sm" onclick={() => session.selectAll()}>Select all</Button>
				<Button variant="secondary" size="sm" disabled={!snapshot.hasSelection} onclick={() => session.deselect()}>Deselect</Button>
			</div>
		</div>
	{:else}
		{#if options.includes('size')}
			<PropertySlider label="Size" value={snapshot.settings.size} min={1} max={200} defaultValue={DEFAULT_SIZE} unit=" px" onchange={(value) => session.setSetting('size', value)} />
		{/if}
		{#if options.includes('opacity')}
			<PropertySlider label="Opacity" value={snapshot.settings.opacity} min={1} max={100} defaultValue={100} unit="%" onchange={(value) => session.setSetting('opacity', value)} />
		{/if}
		{#if options.includes('tolerance')}
			<PropertySlider label="Tolerance" value={snapshot.settings.tolerance} min={0} max={100} defaultValue={32} onchange={(value) => session.setSetting('tolerance', value)} />
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
