<!--
	User-facing model details. No operational/admin data here: no filename, file_path,
	sha256, file_size, indexed_at, backend_ids, and never calls getModelAvailability()
	(that endpoint 403s for non-admins) — `createLibraryModelDetailsController` cannot
	hold or request any of that. Admins get that view in AdminModelDetailsModal, opened
	from the admin Models tab.
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { modelLibraryStore } from '$lib/stores/modelLibrary';
	import BaseModal from './BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import ModelDetailsHeader from './model-details/ModelDetailsHeader.svelte';
	import ModelDetailsContent from './model-details/ModelDetailsContent.svelte';
	import { createLibraryModelDetailsController } from './model-details/modelDetailsController';

	export let isOpen: boolean = false;
	export let modelId: string | null = null;
	export let onClose: () => void;

	const controller = createLibraryModelDetailsController();
	const {
		capabilities,
		model,
		loading,
		currentImageIndex,
		imageFiles,
		displayName,
		selectedTags,
		isFavorite
	} = controller;

	$: collections = $modelLibraryStore.collections;

	$: if (isOpen && modelId) {
		controller.load(modelId);
		if (collections.length === 0) modelLibraryStore.load();
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
				isFavorite={$isFavorite}
				{collections}
				onAddToCollection={controller.addToCollection}
				on:rename={handleRename}
				on:toggleFavorite={controller.toggleFavorite}
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
			onPrevImage={controller.prevImage}
			onNextImage={controller.nextImage}
		/>
	{/if}
</BaseModal>
