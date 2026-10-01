<script lang="ts">
	import AdjustPanel from './AdjustPanel.svelte';
	import CropPanel from './CropPanel.svelte';
	import OptionsPanel from './OptionsPanel.svelte';
	import SelectionPanel from './SelectionPanel.svelte';
	import TransformPanel from './TransformPanel.svelte';
	import type { PaintSession, SessionSnapshot } from './session';

	export let session: PaintSession;
	export let state: SessionSnapshot;

	$: tool = state.tools.find((candidate) => candidate.id === state.toolId);
	$: panel = tool?.panel ?? 'options';
</script>

{#if panel === 'adjust'}
	<AdjustPanel {session} {state} />
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
