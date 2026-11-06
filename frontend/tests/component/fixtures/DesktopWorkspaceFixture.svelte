<script lang="ts">
	import DesktopWorkspace from '../../../src/routes/generate/components/DesktopWorkspace.svelte';
	import GenerationPanels from '../../../src/routes/generate/components/GenerationPanels.svelte';
	import type { GenerationTab } from '$lib/types/tabs';
	import type { PresetInfo } from '$lib/services/api/index';

	export let tab: GenerationTab;
	export let presets: PresetInfo[] = [];
	export let availableModes: Array<{ id: string; label: string }> = [];

	const noop = () => {};
</script>

<DesktopWorkspace
	{tab}
	{presets}
	{availableModes}
	onPresetChange={noop}
	onModeChange={noop}
	onVariantChange={noop}
	onReload={noop}
>
	<GenerationPanels
		{tab}
		tabHandlers={{}}
		promptRelayActive={false}
		canBatch={false}
		isActive
		leftPanelWidth={420}
		dynamicFormRefs={{}}
		{presets}
		onFormDataChange={noop}
		onWorkbenchPrevious={noop}
		onWorkbenchNext={noop}
		onWorkbenchHeightChange={noop}
		onMoveToWorkbench={noop}
	/>
</DesktopWorkspace>
