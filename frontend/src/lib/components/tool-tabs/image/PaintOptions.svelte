<script lang="ts">
	import { Button } from '$lib/components/ui';
	import FrameSheet from '$lib/components/tool-tabs/frame/FrameSheet.svelte';
	import PaintLayers from '$lib/components/paint/PaintLayers.svelte';
	import PaintPanel from '$lib/components/paint/PaintPanel.svelte';
	import PaintToolPanel from '$lib/components/paint/PaintToolPanel.svelte';
	import type { LayerAction } from '$lib/paint/layerActions';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';

	let {
		session,
		state: snapshot,
		phone = false,
		layerActions = [],
		layers = true,
		target = null,
		onSaveFilter = () => {}
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		phone?: boolean;
		layerActions?: LayerAction[];
		layers?: boolean;
		target?: { name: string | null; thumb: string | null } | null;
		onSaveFilter?: () => void;
	} = $props();

	let layersOpen = $state(false);
</script>

{#snippet targetCard()}
	{#if target}
		<div class="flex items-center gap-2.5 rounded-lg border border-line bg-surface-1 p-2" data-paint-target={target.name ? 'layer' : 'new'}>
			<span class="alpha-checker flex h-8 w-8 flex-none items-center justify-center overflow-hidden rounded">
				{#if target.thumb}<img src={target.thumb} alt="" class="h-full w-full object-contain" />{/if}
			</span>
			<span class="flex min-w-0 flex-col">
				<span class="truncate text-base font-medium text-fg">{target.name ? `Painting on ${target.name}` : 'Painting on a new layer'}</span>
				<span class="truncate text-sm text-fg-muted">Strokes go to this layer only</span>
			</span>
		</div>
	{/if}
{/snippet}

{#snippet floatingCard()}
	{#if !layers && snapshot.floatingId !== null}
		<div class="flex items-center gap-2 rounded-lg border border-dashed border-signal bg-signal/10 p-2" data-floating-row>
			<span class="flex min-w-0 flex-1 flex-col">
				<span class="truncate text-base font-medium text-fg">Floating piece</span>
				<span class="truncate text-sm text-signal">Press Enter to place</span>
			</span>
			<Button variant="secondary" size="xs" onclick={() => session.placeFloating()} attrs={{ 'data-floating-place': '' }}>Place</Button>
			<Button size="xs" onclick={() => session.keepFloatingAsLayer()} attrs={{ 'data-floating-keep': '' }}>Keep as layer</Button>
		</div>
	{/if}
{/snippet}

{#if phone}
	<div class="flex flex-col gap-3" data-paint-options="phone">
		{@render targetCard()}
		{@render floatingCard()}
		<PaintToolPanel {session} state={snapshot} {phone} {onSaveFilter} />
		{#if layers}
		<Button variant="secondary" size="sm" icon="layers" class="w-full" ariaHaspopup="dialog" onclick={() => (layersOpen = true)} attrs={{ 'data-paint-layers-button': '' }}>
			Layers
			<span class="font-mono tabular-nums text-fg-subtle">{snapshot.layers.length}</span>
		</Button>
		{/if}
	</div>
	{#if layersOpen}
		<FrameSheet label="Layers" onclose={() => (layersOpen = false)}>
			<div class="px-2 pb-2" data-paint-layers-sheet>
				<PaintLayers {session} state={snapshot} phone actions={layerActions} />
			</div>
		</FrameSheet>
	{/if}
{:else}
	<div class="flex flex-col gap-3" data-paint-options="desktop">
		{@render targetCard()}
		{@render floatingCard()}
		<PaintPanel {session} state={snapshot} {layerActions} withLayers={layers} {onSaveFilter} />
	</div>
{/if}
