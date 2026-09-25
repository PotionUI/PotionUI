<script lang="ts">
	import { confirmDialog } from '$lib/stores/confirm';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { formatBytes } from '$lib/utils/format';
	import { Badge, IconButton, Spinner } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailTabs, DetailBody, DetailLayout, DetailSection, DetailField, DetailFooter } from '$lib/components/detail';
	import ModelMediaViewer from '$lib/components/modals/model-details/ModelMediaViewer.svelte';
	import ModelPreviewGallery from '$lib/components/modals/model-details/ModelPreviewGallery.svelte';
	import ModelGenerationsCard from '$lib/components/modals/model-details/ModelGenerationsCard.svelte';
	import ModelAttributesCard from '$lib/components/modals/model-details/ModelAttributesCard.svelte';
	import ModelInfoCard from '$lib/components/modals/model-details/ModelInfoCard.svelte';
	import ModelFilesCard from '$lib/components/modals/model-details/ModelFilesCard.svelte';
	import ModelTechnicalDetailsCard from '$lib/components/modals/model-details/ModelTechnicalDetailsCard.svelte';
	import ModelAvailabilityCard from '$lib/components/modals/model-details/ModelAvailabilityCard.svelte';
	import ModelMirrorsCard from '$lib/components/modals/model-details/ModelMirrorsCard.svelte';
	import ModelOtherVariants from '$lib/components/recipes/ModelOtherVariants.svelte';
	import { createAdminModelDetailsController } from '$lib/components/modals/model-details/modelDetailsController';
	import { overviewDraftFromModel, overviewDraftIsDirty, type ModelOverviewDraft } from './modelOverviewDraft';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import { createModelAssignmentAdapter } from '$lib/components/assignment/modelAssignmentAdapter';
	import { adminSectionIcon } from '../../adminSections';
	import type { ModelPreviewMedia } from '$lib/utils/modelPreview';

	type DetailTab = 'overview' | 'availability' | 'access' | 'attributes';

	let {
		modelId,
		onBack,
		onDeleted,
		onAssignChanged
	}: {
		modelId: string;
		onBack: () => void;
		onDeleted: () => void;
		onAssignChanged: (change: { userCount: number; groupCount: number }) => void;
	} = $props();

	let activeTab = $state<DetailTab>('overview');
	let deleting = $state(false);

	const controller = createAdminModelDetailsController();
	const {
		model,
		loading,
		currentImageIndex,
		imageFiles,
		displayName,
		selectedTags,
		selectedTagIds,
		availability,
		availabilityLoading
	} = controller;

	$effect(() => {
		activeTab = 'overview';
		controller.load(modelId);
	});


	let overviewDraft = $state<ModelOverviewDraft>({ description: '', promptingGuidance: '' });
	let overviewSnapshot = $state<ModelOverviewDraft>({ description: '', promptingGuidance: '' });
	let overviewSeededFor: string | null = null;
	let overviewSaving = $state(false);

	$effect(() => {
		if ($model && overviewSeededFor !== $model.id) {
			overviewSeededFor = $model.id;
			overviewDraft = overviewDraftFromModel($model);
			overviewSnapshot = { ...overviewDraft };
		}
	});

	const overviewDirty = $derived(overviewDraftIsDirty(overviewDraft, overviewSnapshot));

	function discardOverview() {
		overviewDraft = { ...overviewSnapshot };
	}

	async function saveOverview() {
		if (!$model) return;
		overviewSaving = true;
		try {
			if (overviewDraft.description !== overviewSnapshot.description) {
				await controller.saveDescription(overviewDraft.description);
			}
			if (overviewDraft.promptingGuidance !== overviewSnapshot.promptingGuidance) {
				await controller.savePromptingGuidance(overviewDraft.promptingGuidance);
			}
			overviewSnapshot = { ...overviewDraft };
		} finally {
			overviewSaving = false;
		}
	}

	let generationsTotal = $state(0);


	let attributesCardRef: ModelAttributesCard | undefined = $state();
	let attributesDirty = $state(false);
	let attributesSaving = $state(false);

	function saveAttributes() {
		return attributesCardRef?.commit();
	}

	function discardAttributes() {
		attributesCardRef?.discard();
	}

	function handleTagsChange(event: CustomEvent<string[]>) {
		controller.updateTags(event.detail);
	}

	function handlePrimaryChange(
		event: CustomEvent<{ file_id?: string | null; url: string; type: string; name?: string | null } | null>
	) {
		controller.handlePrimaryPreviewChange(event.detail as ModelPreviewMedia | null);
	}

	function handleAssignmentChanged(event: CustomEvent<{ userCount: number; groupCount: number }>) {
		onAssignChanged(event.detail);
	}

	async function handleDelete() {
		const name = $displayName || $model?.filename || modelId;
		if (
			!(await confirmDialog({
				title: `Are you sure you want to remove "${name}" from the index?`,
				message: 'This will not delete the file.',
				variant: 'danger'
			}))
		)
			return;
		deleting = true;
		try {
			const response = await api.deleteModel(modelId);
			if (response.success) onDeleted();
		} catch (error) {
			logger.error('Error deleting model:', error);
		} finally {
			deleting = false;
		}
	}

	const tabs: { id: DetailTab; label: string; icon: string }[] = [
		{ id: 'overview', label: 'Overview', icon: 'info' },
		{ id: 'availability', label: 'Availability', icon: 'database' },
		{ id: 'access', label: 'Access', icon: 'group' },
		{ id: 'attributes', label: 'Attributes', icon: 'sliders' }
	];
</script>

<div class="flex h-full flex-col">
	{#if $loading || !$model}
		<DetailHeader title="Loading…" icon={adminSectionIcon('models')} backLabel="Models" {onBack} />
		<div class="flex flex-1 items-center justify-center"><Spinner size="lg" /></div>
	{:else}
		<DetailHeader title={$displayName} icon={adminSectionIcon('models')} backLabel="Models" {onBack}>
			{#snippet chips()}
				<Badge size="sm" variant="neutral" class="uppercase">{$model.model_type}</Badge>
				{#if $model.model_metadata?.base_model}
					<Badge size="sm" variant="signal">{String($model.model_metadata.base_model)}</Badge>
				{/if}
				{#if $model.file_size}
					<Badge size="sm" class="font-mono tabular-nums">{formatBytes($model.file_size)}</Badge>
				{/if}
			{/snippet}
			{#snippet subtitle()}
				{$model.filename}
			{/snippet}
			{#snippet actions()}
				<Tooltip text="Remove from index">
					<IconButton
						icon="trash"
						label="Remove from index"
						class="text-danger hover:text-danger hover:bg-danger/10"
						disabled={deleting}
						onclick={handleDelete}
					/>
				</Tooltip>
			{/snippet}
		</DetailHeader>

		<DetailTabs {tabs} active={activeTab} onSelect={(id) => (activeTab = id as DetailTab)} ariaLabel="Model details" />

		<DetailBody>
			{#if activeTab === 'overview'}
				<DetailLayout>
					{#snippet main()}
						<DetailSection label="Overview">
							<div class="space-y-4">
								<DetailField
									label="Description"
									id="model-description"
									wide
									help="Markdown is supported (headings, lists, links, code, tables)."
								>
									<textarea
										id="model-description"
										class="input font-mono text-sm"
										rows="8"
										bind:value={overviewDraft.description}
										placeholder="Add your notes, tips, or recommended settings for this model..."
									></textarea>
								</DetailField>
								<DetailField
									label="Prompting guidance"
									id="model-prompting-guidance"
									wide
									help="Admin-only. Shown to the chat assistant when this model is active — it never appears to users, who only see that guidance was applied."
								>
									<textarea
										id="model-prompting-guidance"
										class="input text-sm"
										rows="6"
										bind:value={overviewDraft.promptingGuidance}
										placeholder="e.g. Favor short, comma-separated tags over full sentences; always include a quality tag like 'masterpiece'..."
									></textarea>
								</DetailField>
							</div>
						</DetailSection>
						<ModelOtherVariants modelId={$model.id} />
						<DetailSection label="Previews">
							<ModelPreviewGallery bare modelId={$model.id} on:primarychange={handlePrimaryChange} />
						</DetailSection>
						<DetailSection label="Generations">
							{#snippet headerExtra()}
								{#if generationsTotal > 0}
									<Badge size="sm" class="font-mono tabular-nums">{generationsTotal}</Badge>
								{/if}
							{/snippet}
							<ModelGenerationsCard
								modelId={$model.id}
								headerless
								onTotalChange={(total) => (generationsTotal = total)}
							/>
						</DetailSection>
					{/snippet}
					{#snippet aside()}
						<DetailSection label="Preview" padded={false}>
							<div class="h-[280px] flex">
								<ModelMediaViewer
									files={$imageFiles}
									currentIndex={$currentImageIndex}
									displayName={$displayName}
									selectedTags={$selectedTags}
									tagsEditable
									selectedTagIds={$selectedTagIds}
									on:prev={controller.prevImage}
									on:next={controller.nextImage}
									on:tagsChange={handleTagsChange}
								/>
							</div>
						</DetailSection>
						<DetailSection label="Information">
							<ModelInfoCard bare modelId={$model.id} modelType={$model.model_type} createdAt={$model.created_at} />
						</DetailSection>
						<DetailSection label="Files">
							<ModelFilesCard bare files={$model.files || []} />
						</DetailSection>
					{/snippet}
				</DetailLayout>
			{:else if activeTab === 'availability'}
				<DetailLayout>
					{#snippet main()}
						<DetailSection label="Availability">
							{#snippet headerExtra()}
								{#if $availability && $availability.availability.length > 0}
									<Badge size="sm" class="font-mono tabular-nums">{$availability.availability.length}</Badge>
								{/if}
							{/snippet}
							<ModelAvailabilityCard bare availability={$availability} loading={$availabilityLoading} expectedDigest={$model.sha256} />
						</DetailSection>
						<DetailSection label="Mirrors">
							{#snippet headerExtra()}
								{#if $model.providers.length > 0}
									<Badge size="sm" class="font-mono tabular-nums">{$model.providers.length}</Badge>
								{/if}
							{/snippet}
							<ModelMirrorsCard bare providers={$model.providers} />
						</DetailSection>
					{/snippet}
					{#snippet aside()}
						<DetailSection label="Technical details">
							<ModelTechnicalDetailsCard
								bare
								filename={$model.filename}
								filePath={$model.file_path}
								sha256={$model.sha256}
								fileSize={$model.file_size}
								indexedAt={$model.indexed_at}
							/>
						</DetailSection>
					{/snippet}
				</DetailLayout>
			{:else if activeTab === 'access'}
				<DetailLayout>
					{#snippet main()}
						{#key $model.id}
							<AssignmentCard
								adapter={createModelAssignmentAdapter($model.id)}
								resourceKey={$model.id}
								resourceName={$displayName}
								on:changed={handleAssignmentChanged}
							/>
						{/key}
					{/snippet}
				</DetailLayout>
			{:else}
				<DetailLayout>
					{#snippet main()}
						<DetailSection label="Attributes">
							<ModelAttributesCard
								bind:this={attributesCardRef}
								model={$model}
								editable
								footerMode
								bind:dirty={attributesDirty}
								bind:saving={attributesSaving}
							/>
						</DetailSection>
					{/snippet}
				</DetailLayout>
			{/if}
		</DetailBody>

		{#if activeTab === 'overview'}
			<DetailFooter dirtyCount={overviewDirty ? 1 : 0} saving={overviewSaving} onSave={saveOverview} onDiscard={discardOverview} />
		{:else if activeTab === 'attributes'}
			<DetailFooter dirtyCount={attributesDirty ? 1 : 0} saving={attributesSaving} onSave={saveAttributes} onDiscard={discardAttributes} />
		{/if}
	{/if}
</div>
