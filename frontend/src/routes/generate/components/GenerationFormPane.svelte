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
		resourceUseCountsEqual,
		tabResourceSegmentGroups,
		type PromptResourceUsage,
		type ResourceUseCounts
	} from '$lib/utils/promptResourceUsage';
	import type { Tab } from '$lib/types/tabs';

	const NO_FIELD_ERRORS: Record<string, string[]> = {};
	const NO_COUNTS: ResourceUseCounts = {};

	let {
		tab,
		onFormDataChange,
		formRef = $bindable(undefined),
		videoDirectorActive = false
	}: {
		tab: Tab;
		onFormDataChange: (data: Record<string, unknown>) => void;
		formRef?: DynamicForm | undefined;
		videoDirectorActive?: boolean;
	} = $props();

	const resourceUsage = writable<PromptResourceUsage>({ specs: [], counts: {} });
	setContext(PROMPT_RESOURCE_USAGE_CONTEXT_KEY, resourceUsage);

	const tabId = $derived(tab.id);
	const presetId = $derived(tab.selectedPreset);
	const mode = $derived(tab.selectedMode);
	const variant = $derived(tab.selectedVariant ?? undefined);
	const formData = $derived(tab.formData);
	const promptSegments = $derived(tab.promptSegments);
	const negativePromptSegments = $derived(tab.negativePromptSegments);
	const promptTabs = $derived(tab.promptTabs);
	const fieldErrors = $derived($formValidationStore[tabId] ?? NO_FIELD_ERRORS);
	const sectionCollapsedContext = $derived(createSectionCollapsedController(tabId));

	let resourceSpecs = $state.raw<PromptResourceSpec[]>([]);

	$effect(() => {
		const key = `${presetId ?? ''}::${mode ?? ''}::${variant ?? ''}`;
		let live = true;
		resourceSpecs = [];
		getPresetPromptResources(presetId, mode, variant).then((result) => {
			if (live && key === `${presetId ?? ''}::${mode ?? ''}::${variant ?? ''}`) resourceSpecs = result.specs;
		});
		return () => {
			live = false;
		};
	});

	const resourceCounts = $derived.by(() => {
		if (!resourceSpecs.length) return NO_COUNTS;
		return countResourceReferences(
			tabResourceSegmentGroups({ promptSegments, negativePromptSegments, promptTabs })
		);
	});

	let publishedCounts: ResourceUseCounts = NO_COUNTS;
	let publishedSpecs: PromptResourceSpec[] = [];

	$effect(() => {
		const specs = resourceSpecs;
		const counts = resourceCounts;
		if (specs === publishedSpecs && resourceUseCountsEqual(counts, publishedCounts)) return;
		publishedSpecs = specs;
		publishedCounts = counts;
		resourceUsage.set({ specs, counts });
	});
</script>

<DynamicForm
	bind:this={formRef}
	{tabId}
	presetId={presetId ?? ''}
	mode={mode ?? undefined}
	formName="generation_form"
	{variant}
	initialData={formData}
	{videoDirectorActive}
	{onFormDataChange}
	{fieldErrors}
	onFieldEdit={(name) => formValidationStore.clearField(tabId, name)}
	onSchemaKeyChange={() => formValidationStore.clearAll(tabId)}
	{sectionCollapsedContext}
/>
