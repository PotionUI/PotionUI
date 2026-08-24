<script lang="ts">
	import DynamicForm from '$lib/components/DynamicForm.svelte';
	import { formValidationStore } from '$lib/stores/formValidation';
	import { createSectionCollapsedController } from '$lib/utils/sectionCollapsedController';
	import type { Tab } from '$lib/types/tabs';

	// The generation form, wired the same way at every mount site (mobile
	// Panel 1, desktop GenerationPanels left pane). `formRef` is a two-way
	// binding so the caller's `dynamicFormRefs[tab.id]` map keeps working
	// (handlePresetReload reads `.forceReload()` off it).
	export let tab: Tab;
	export let onFormDataChange: (data: Record<string, unknown>) => void;
	export let formRef: DynamicForm | undefined = undefined;
	export let videoDirectorActive = false;
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
