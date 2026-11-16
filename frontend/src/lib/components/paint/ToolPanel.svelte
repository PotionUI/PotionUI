<script lang="ts">
	import AdjustPanel from '$lib/components/imageEditor/AdjustPanel.svelte';
	import CropPanel from '$lib/components/imageEditor/CropPanel.svelte';
	import FiltersPanel from '$lib/components/imageEditor/FiltersPanel.svelte';
	import OptionsPanel from '$lib/components/imageEditor/OptionsPanel.svelte';
	import SelectionPanel from '$lib/components/imageEditor/SelectionPanel.svelte';
	import TransformPanel from '$lib/components/imageEditor/TransformPanel.svelte';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let phone = false;
	export let onSaveFilter: () => void = () => {};

	$: tool = state.tools.find((candidate) => candidate.id === state.toolId);
	$: panel = tool?.panel ?? 'options';
</script>

{#if panel === 'adjust'}
	<AdjustPanel {session} {state} {onSaveFilter} />
{:else if panel === 'filters'}
	<FiltersPanel {session} {state} {phone} {onSaveFilter} />
{:else if panel === 'crop'}
	<CropPanel {session} {state} />
{:else if panel === 'transform'}
	<TransformPanel {session} {state} />
{:else if panel === 'selection'}
	<SelectionPanel {session} {state} {tool} />
{:else}
	<OptionsPanel
		{tool}
		settings={state.settings}
		onSize={(value) => session.setSetting('size', value)}
		onOpacity={(value) => session.setSetting('opacity', value)}
		onColor={(value) => session.setColor(value)}
		onTolerance={(value) => session.setSetting('tolerance', value)}
	/>
{/if}
