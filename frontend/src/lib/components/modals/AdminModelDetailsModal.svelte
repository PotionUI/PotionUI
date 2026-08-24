<!--
	Admin-facing model details: everything ModelDetailsContent shows, plus the operational
	block (filename/file_path/sha256/file_size/indexed_at), per-backend Availability, and
	the edit affordances for description/attributes (trigger words included)/tags/prompting
	guidance. Only ever opened from the admin Models tab.
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import BaseModal from './BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import ModelDetailsHeader from './model-details/ModelDetailsHeader.svelte';
	import ModelDetailsContent from './model-details/ModelDetailsContent.svelte';
	import { createAdminModelDetailsController } from './model-details/modelDetailsController';

	export let isOpen: boolean = false;
	export let modelId: string | null = null;
	export let onClose: () => void;

	const controller = createAdminModelDetailsController();
	const {
		capabilities,
		model,
		loading,
		currentImageIndex,
		imageFiles,
		displayName,
		selectedTags,
		selectedTagIds,
		availability,
		availabilityLoading,
		savingDescription,
		savingPromptingGuidance
	} = controller;

	$: if (isOpen && modelId) {
		controller.load(modelId);
	}

	$: if (!isOpen) {
		// Clear so a subsequent open never flashes stale availability for a different model.
		controller.reset();
	}

	function handleRename(event: CustomEvent<string>) {
		controller.rename(event.detail);
	}

	function handleKeyDown(event: KeyboardEvent) {
		if (!isOpen) return;
		controller.handleKeydownNav(event);
	}

	onMount(() => {
		window.addEventListener('keydown', handleKeyDown);
	});

	onDestroy(() => {
		window.removeEventListener('keydown', handleKeyDown);
	});
</script>

<BaseModal
	{isOpen}
	title="Model Details"
	sizeClass="md:w-[90vw] md:h-[85vh]"
	on:close={onClose}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="model" className="w-5 h-5 text-fg-muted flex-shrink-0" />
	</svelte:fragment>
	<svelte:fragment slot="header">
		{#if $model}
			<ModelDetailsHeader
				modelType={$model.model_type}
				displayName={$displayName}
				customName={$model.custom_name}
				showLibraryActions={false}
				on:rename={handleRename}
			/>
		{/if}
	</svelte:fragment>

	<!-- Body (height must fill the modal) -->
	{#if $loading}
		<div class="flex-1 flex items-center justify-center h-full">
			<Spinner size="lg" />
		</div>
	{:else if $model}
		<ModelDetailsContent
			{capabilities}
			variant="modal"
			model={$model}
			currentImageIndex={$currentImageIndex}
			imageFiles={$imageFiles}
			displayName={$displayName}
			selectedTags={$selectedTags}
			selectedTagIds={$selectedTagIds}
			savingDescription={$savingDescription}
			savingPromptingGuidance={$savingPromptingGuidance}
			availability={$availability}
			availabilityLoading={$availabilityLoading}
			onPrevImage={controller.prevImage}
			onNextImage={controller.nextImage}
			onTagsChange={controller.updateTags}
			onSaveDescription={controller.saveDescription}
			onSavePromptingGuidance={controller.savePromptingGuidance}
			onPrimaryPreviewChange={controller.handlePrimaryPreviewChange}
		/>
	{/if}
</BaseModal>
