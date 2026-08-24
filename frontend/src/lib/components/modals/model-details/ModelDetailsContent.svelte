<!--
	The body every model-details surface renders: description (+ admin-only
	prompting guidance) and generations on the left, media + attributes
	(including trigger words - see ModelAttributesCard.svelte) + info +
	(admin-only technical/availability) + files on the right. Shared by
	AdminModelDetailsModal, ModelDetailsModal and routes/models/[model_id].

	`capabilities` decides which admin-only cards mount. It is fixed per scope
	by `resolveModelDetailsCapabilities` before any model ever loads — never
	derived from `model` itself. The `model` prop's *type* only carries
	operational fields (file_path/sha256/file_size/indexed_at/prompting_guidance)
	when `capabilities.scope === 'admin'`, because only
	`createAdminModelDetailsController` ever constructs a value with them; the
	library controller's `model` store cannot hold them in the first place. See
	`modelDetailsController.ts`.
-->
<script lang="ts">
	import ModelMediaViewer from './ModelMediaViewer.svelte';
	import ExpandableEditableTextCard from './ExpandableEditableTextCard.svelte';
	import ModelPreviewGallery from './ModelPreviewGallery.svelte';
	import ModelGenerationsCard from './ModelGenerationsCard.svelte';
	import ModelAttributesCard from './ModelAttributesCard.svelte';
	import ModelInfoCard from './ModelInfoCard.svelte';
	import ModelFilesCard from './ModelFilesCard.svelte';
	import ModelTechnicalDetailsCard from './ModelTechnicalDetailsCard.svelte';
	import ModelAvailabilityCard from './ModelAvailabilityCard.svelte';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import { createModelAssignmentAdapter } from '$lib/components/assignment/modelAssignmentAdapter';
	import type { ModelDetailsCapabilities, ModelSummary, AdminModelDetails } from './modelDetailsController';
	import type { ModelAvailabilityResponse } from '$lib/types/models';
	import type { ModelPreviewMedia } from '$lib/utils/modelPreview';

	type Variant = 'modal' | 'page';

	let {
		capabilities,
		variant = 'modal',
		model,
		currentImageIndex,
		imageFiles,
		displayName,
		selectedTags,
		selectedTagIds = [],
		savingDescription = false,
		savingPromptingGuidance = false,
		availability = null,
		availabilityLoading = false,
		onPrevImage,
		onNextImage,
		onTagsChange = () => {},
		onSaveDescription = () => {},
		onSavePromptingGuidance = () => {},
		onPrimaryPreviewChange = () => {}
	}: {
		capabilities: ModelDetailsCapabilities;
		variant?: Variant;
		model: ModelSummary | AdminModelDetails | null;
		currentImageIndex: number;
		imageFiles: any[];
		displayName: string;
		selectedTags: Array<{ id: string; name: string }>;
		selectedTagIds?: string[];
		savingDescription?: boolean;
		savingPromptingGuidance?: boolean;
		availability?: ModelAvailabilityResponse | null;
		availabilityLoading?: boolean;
		onPrevImage: () => void;
		onNextImage: () => void;
		onTagsChange?: (tagIds: string[]) => void;
		onSaveDescription?: (value: string) => void;
		onSavePromptingGuidance?: (value: string) => void;
		onPrimaryPreviewChange?: (preview: ModelPreviewMedia | null) => void;
	} = $props();

	// `capabilities.canViewOperationalDetails` is fixed per scope, not derived
	// from `model` — for the library scope this branch never evaluates, and
	// `model` never has these fields to read regardless.
	let adminModel = $derived(
		capabilities.canViewOperationalDetails ? (model as AdminModelDetails | null) : null
	);

	const containerClasses: Record<Variant, string> = {
		modal: 'flex flex-col md:flex-row h-full min-h-0',
		page: 'flex flex-col md:flex-row gap-6 items-start'
	};

	const leftColumnClasses: Record<Variant, string> = {
		modal: 'flex-1 min-h-0 overflow-y-auto',
		page: 'flex-1 min-w-0 w-full'
	};

	const leftColumnInnerClasses: Record<Variant, string> = {
		modal: 'p-4 md:p-6 space-y-6',
		page: 'space-y-6'
	};

	const rightColumnClasses: Record<Variant, string> = {
		modal: 'w-full md:w-[440px] shrink-0 flex flex-col max-h-[45vh] md:max-h-none border-t md:border-t-0 md:border-l border-line bg-surface-1 overflow-y-auto min-h-0',
		page: 'w-full md:w-[440px] shrink-0 space-y-4'
	};

	const mediaBoxClasses: Record<Variant, string> = {
		modal: 'h-[320px] shrink-0 flex',
		page: 'h-[400px] shrink-0 flex rounded-lg overflow-hidden border border-line bg-surface-1 shadow-raised'
	};

	function handleTagsChange(event: CustomEvent<string[]>) {
		onTagsChange(event.detail);
	}

	function handlePrimaryChange(
		event: CustomEvent<{ file_id?: string | null; url: string; type: string; name?: string | null } | null>
	) {
		onPrimaryPreviewChange(event.detail as ModelPreviewMedia | null);
	}
</script>

<div class={containerClasses[variant]}>
	<div class={leftColumnClasses[variant]}>
		<div class={leftColumnInnerClasses[variant]}>
			<ExpandableEditableTextCard
				title="Description"
				value={model?.description}
				editable={capabilities.canEditMetadata}
				saving={savingDescription}
				renderMode="markdown"
				placeholder="Add your notes, tips, or recommended settings for this model..."
				help="Markdown is supported (headings, lists, links, code, tables)."
				emptyText="No description available"
				editableEmptyText="Click edit to add your notes and tips..."
				onSave={onSaveDescription}
			/>

			{#if capabilities.canEditPromptingGuidance}
				<ExpandableEditableTextCard
					title="Prompting Guidance"
					value={adminModel?.prompting_guidance}
					saving={savingPromptingGuidance}
					renderMode="plain"
					placeholder="e.g. Favor short, comma-separated tags over full sentences; always include a quality tag like 'masterpiece'..."
					help="Admin-only. Shown to the chat assistant when this model is active — it never appears to users, who only see that guidance was applied."
					emptyText="Click edit to teach the chat assistant how to write prompts for this model..."
					onSave={onSavePromptingGuidance}
				/>
			{/if}

			{#if capabilities.canManageAssignments && model}
				<div>
					<h3 class="text-sm font-semibold text-fg mb-1">Access</h3>
					<p class="text-xs text-fg-muted mb-3">Assign this model directly to specific users or grant it to every member of a user group.</p>
					{#key model.id}
						<AssignmentCard
							adapter={createModelAssignmentAdapter(model.id)}
							resourceKey={model.id}
							resourceName={displayName}
						/>
					{/key}
				</div>
			{/if}

			{#if model}
				<ModelGenerationsCard modelId={model.id} />
			{/if}
		</div>
	</div>

	<div class={rightColumnClasses[variant]}>
		<div class={mediaBoxClasses[variant]}>
			<ModelMediaViewer
				files={imageFiles}
				currentIndex={currentImageIndex}
				{displayName}
				{selectedTags}
				tagsEditable={capabilities.canEditMetadata}
				{selectedTagIds}
				on:prev={onPrevImage}
				on:next={onNextImage}
				on:tagsChange={handleTagsChange}
			/>
		</div>

		<div class={variant === 'modal' ? 'p-4 space-y-4' : 'space-y-4'}>
			{#if capabilities.canManagePreviewGallery && model}
				<ModelPreviewGallery modelId={model.id} on:primarychange={handlePrimaryChange} />
			{/if}

			<ModelAttributesCard {model} editable={capabilities.canEditMetadata} />

			{#if model}
				<ModelInfoCard modelId={model.id} modelType={model.model_type} createdAt={model.created_at} />
			{/if}

			{#if capabilities.canViewOperationalDetails && adminModel}
				<!-- Admin-only technical details: filename/path/hash/size/indexed-at are
				     operational detail, not something a generating user needs to know. -->
				<ModelTechnicalDetailsCard
					filename={adminModel.filename}
					filePath={adminModel.file_path}
					sha256={adminModel.sha256}
					fileSize={adminModel.file_size}
					indexedAt={adminModel.indexed_at}
				/>
			{/if}

			{#if capabilities.canViewAvailability}
				<ModelAvailabilityCard
					{availability}
					loading={availabilityLoading}
					expectedDigest={adminModel?.sha256}
				/>
			{/if}

			<ModelFilesCard files={model?.files || []} />
		</div>
	</div>
</div>
