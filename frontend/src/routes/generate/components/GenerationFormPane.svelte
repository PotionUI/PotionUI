<script lang="ts">
	import { setContext } from 'svelte';
	import { writable } from 'svelte/store';
	import DynamicForm from '$lib/components/DynamicForm.svelte';
	import { formValidationStore } from '$lib/stores/formValidation';
	import { createSectionCollapsedController } from '$lib/utils/sectionCollapsedController';
	import { getPresetPromptResources } from '$lib/utils/presetPromptResourcesCache';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import {
		PROMPT_RESOURCE_USAGE_CONTEXT_KEY,
		countResourceReferences,
		tabResourceSegmentGroups,
		type PromptResourceUsage
	} from '$lib/utils/promptResourceUsage';
	import type { Tab } from '$lib/types/tabs';

	// The generation form, wired the same way at every mount site (mobile
	// Panel 1, desktop GenerationPanels left pane). `formRef` is a two-way
	// binding so the caller's `dynamicFormRefs[tab.id]` map keeps working
	// (handlePresetReload reads `.forceReload()` off it).
	export let tab: Tab;
	export let onFormDataChange: (data: Record<string, unknown>) => void;
	export let formRef: DynamicForm | undefined = undefined;
	export let videoDirectorActive = false;

	const resourceUsage = writable<PromptResourceUsage>({ specs: [], counts: {} });
	setContext(PROMPT_RESOURCE_USAGE_CONTEXT_KEY, resourceUsage);

	let resourceSpecs: PromptResourceSpec[] = [];
	let specsKey = '';

	$: {
		const preset = tab.selectedPreset;
		const mode = tab.selectedMode;
		const variant = tab.selectedVariant ?? undefined;
		const key = `${preset ?? ''}::${mode ?? ''}::${variant ?? ''}`;
		if (key !== specsKey) {
			specsKey = key;
			resourceSpecs = [];
			getPresetPromptResources(preset, mode, variant).then((result) => {
				if (specsKey === key) resourceSpecs = result.specs;
			});
		}
	}

	$: resourceUsage.set({
		specs: resourceSpecs,
		counts: resourceSpecs.length ? countResourceReferences(tabResourceSegmentGroups(tab)) : {}
	});
</script>

<DynamicForm
	bind:this={formRef}
	tabId={tab.id}
	presetId={tab.selectedPreset ?? ''}
	mode={tab.selectedMode ?? undefined}
	formName="generation_form"
	variant={tab.selectedVariant ?? undefined}
	initialData={tab.formData}
	{videoDirectorActive}
	{onFormDataChange}
	fieldErrors={$formValidationStore[tab.id] ?? {}}
	onFieldEdit={(name) => formValidationStore.clearField(tab.id, name)}
	onSchemaKeyChange={() => formValidationStore.clearAll(tab.id)}
	sectionCollapsedContext={createSectionCollapsedController(tab.id)}
/>
