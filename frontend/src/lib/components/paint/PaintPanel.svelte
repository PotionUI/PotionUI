<script lang="ts">
	import PaintLayers from '$lib/components/paint/PaintLayers.svelte';
	import PaintToolPanel from '$lib/components/paint/PaintToolPanel.svelte';
	import type { LayerAction } from '$lib/paint/layerActions';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';

	let {
		session,
		state: snapshot,
		phone = false,
		layerActions = [],
		onSaveFilter = () => {}
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		phone?: boolean;
		layerActions?: LayerAction[];
		onSaveFilter?: () => void;
	} = $props();

	let kind = $derived(snapshot.tools.find((candidate) => candidate.id === snapshot.toolId)?.panel ?? 'options');
	let withLayers = $derived(kind === 'options' || kind === 'selection' || kind === 'transform');
</script>

<div class="flex flex-col gap-4" data-paint-panel>
	<PaintToolPanel {session} state={snapshot} {phone} {onSaveFilter} />
	{#if withLayers}
		<div class="-mx-4 border-t border-line px-4 pt-4">
			<PaintLayers {session} state={snapshot} {phone} actions={layerActions} />
		</div>
	{/if}
</div>
