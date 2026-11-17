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
		onSaveFilter = () => {}
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		phone?: boolean;
		layerActions?: LayerAction[];
		layers?: boolean;
		onSaveFilter?: () => void;
	} = $props();

	let layersOpen = $state(false);
</script>

{#if phone}
	<div class="flex flex-col gap-3" data-paint-options="phone">
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
	<div data-paint-options="desktop">
		<PaintPanel {session} state={snapshot} {layerActions} withLayers={layers} {onSaveFilter} />
	</div>
{/if}
