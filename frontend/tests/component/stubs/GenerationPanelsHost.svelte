<script lang="ts">
	import { generationTabs } from '$lib/types/tabs';
	import { tabsStore } from '$lib/stores/tabs';
	import { formDataPublicationPatch } from '$lib/utils/sessionTabState';
	import GenerationPanels from '../../../src/routes/generate/components/GenerationPanels.svelte';

	export let tabId: string;

	$: tab = generationTabs($tabsStore.tabs).find((candidate) => candidate.id === tabId)!;

	const noop = () => {};

	function handleFormDataChange(data: Record<string, unknown>) {
		const current = generationTabs($tabsStore.tabs).find((candidate) => candidate.id === tabId);
		if (!current) return;
		tabsStore.updateTab(tabId, formDataPublicationPatch(current, data));
	}
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
	onFormDataChange={handleFormDataChange}
	onWorkbenchPrevious={noop}
	onWorkbenchNext={noop}
	onWorkbenchHeightChange={noop}
	onMoveToWorkbench={noop}
/>
