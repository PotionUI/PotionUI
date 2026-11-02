<script lang="ts">
	import { generationTabs } from '$lib/types/tabs';
	import { tabsStore } from '$lib/stores/tabs';
	import GenerationPanels from '../../../src/routes/generate/components/GenerationPanels.svelte';

	export let tabId: string;

	$: tab = generationTabs($tabsStore.tabs).find((candidate) => candidate.id === tabId)!;

	const noop = () => {};
</script>

<GenerationPanels
	{tab}
	tabHandlers={{}}
	promptRelayActive={false}
	canBatch={false}
	isActive
	leftPanelWidth={420}
	dynamicFormRefs={{}}
	presets={[]}
	onFormDataChange={noop}
	onWorkbenchPrevious={noop}
	onWorkbenchNext={noop}
	onWorkbenchHeightChange={noop}
	onMoveToWorkbench={noop}
/>
