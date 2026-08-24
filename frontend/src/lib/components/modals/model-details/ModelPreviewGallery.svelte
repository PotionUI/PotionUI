<!--
	Admin-only: a model's full preview gallery. Adds go through the standard
	MediaLoaderField (upload/paste/drop/history/library), reorderable by drag.
	The first tile is always the "primary" preview, mirrored server-side onto
	`model.preview_media`; reports the new primary via `on:primarychange` so the
	parent modal's `model` stays in sync without a refetch.
-->
<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner, IconButton } from '$lib/components/ui';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import type { ModelPreviewMediaItem } from '$lib/utils/modelPreview';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	export let modelId: string;

	const dispatch = createEventDispatcher<{
		primarychange: { file_id?: string | null; url: string; type: string; name?: string | null } | null;
	}>();

	let previews: ModelPreviewMediaItem[] = [];
	let loading = true;
	let listError: string | null = null;

	// A single "add" slot: MediaLoaderField always mints its own value (already
	// uploaded to /api/media/upload); once one arrives it's registered as a
	// preview and the slot is reset back to empty for the next add.
	let pendingValue: unknown = null;

	onMount(() => {
		loadPreviews();
	});

	async function loadPreviews() {
		loading = true;
		listError = null;
		try {
			const response = await api.listModelPreviews(modelId);
			if (response.success) {
				previews = response.data?.previews ?? [];
			} else {
				listError = response.message || 'Failed to load previews';
			}
		} catch (error) {
			logger.error('Failed to load model previews:', error);
			listError = getErrorMessage(error);
		} finally {
			loading = false;
		}
	}

	function dispatchPrimary() {
		const primary = previews[0];
		if (!primary) {
			dispatch('primarychange', null);
			return;
		}
		dispatch('primarychange', {
			file_id: primary.file_id,
			url: primary.url,
			type: primary.type,
			name: primary.name
		});
	}

	/**
	 * MediaLoaderField's onChange value is served content already uploaded to
	 * /api/media/upload (or picked from history/library) - it carries the
	 * relative path the preview endpoints expect as `source_path` directly, so
	 * there's nothing left to resolve before registering it.
	 */
	async function addPreviewFromMedia(item: {
		path?: string;
		relative_path?: string;
		type?: string;
		name?: string;
	}) {
		const mediaType = item.type as 'image' | 'video' | 'audio' | undefined;
		const sourcePath = item.relative_path || item.path;
		if (!mediaType || !sourcePath) return;

		listError = null;
		try {
			const response = await api.addModelPreview(modelId, {
				source_path: sourcePath,
				type: mediaType,
				name: item.name
			});
			if (!response.success) throw new Error(response.message || 'Failed to add preview');
			previews = response.data?.previews ?? previews;
			dispatchPrimary();
		} catch (error) {
			logger.error('Failed to add preview:', error);
			listError = getErrorMessage(error);
		}
	}

	function handlePendingChange(_name: string, value: unknown) {
		if (value && typeof value === 'object') {
			addPreviewFromMedia(value as { path?: string; relative_path?: string; type?: string; name?: string });
		}
		pendingValue = null;
	}

	async function removePreview(previewId: string) {
		const before = previews;
		previews = previews.filter((p) => p.id !== previewId);
		try {
			const response = await api.deleteModelPreview(modelId, previewId);
			if (!response.success) throw new Error(response.message || 'Failed to remove preview');
			previews = response.data?.previews ?? previews;
			dispatchPrimary();
		} catch (error) {
			logger.error('Failed to remove preview:', error);
			previews = before; // roll back the optimistic removal
			listError = getErrorMessage(error);
		}
	}

	async function persistOrder(next: ModelPreviewMediaItem[]) {
		const before = previews;
		previews = next;
		try {
			const response = await api.reorderModelPreviews(
				modelId,
				next.map((p) => p.id)
			);
			if (!response.success) throw new Error(response.message || 'Failed to reorder previews');
			previews = response.data?.previews ?? next;
			dispatchPrimary();
		} catch (error) {
			logger.error('Failed to reorder previews:', error);
			previews = before; // roll back
			listError = getErrorMessage(error);
		}
	}

	function makePrimary(previewId: string) {
		const target = previews.find((p) => p.id === previewId);
		if (!target || previews[0]?.id === previewId) return;
		persistOrder([target, ...previews.filter((p) => p.id !== previewId)]);
	}

	// --- Drag-to-reorder among existing tiles ---
	let dragFromId: string | null = null;

	function onTileDragStart(event: DragEvent, id: string) {
		dragFromId = id;
		event.dataTransfer?.setData('text/plain', id);
		if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
	}

	function onTileDragOver(event: DragEvent) {
		event.preventDefault();
	}

	function onTileDrop(event: DragEvent, targetId: string) {
		event.preventDefault();
		event.stopPropagation();
		const fromId = dragFromId;
		dragFromId = null;
		if (!fromId || fromId === targetId) return;

		const fromIndex = previews.findIndex((p) => p.id === fromId);
		const toIndex = previews.findIndex((p) => p.id === targetId);
		if (fromIndex === -1 || toIndex === -1) return;

		const next = [...previews];
		const [moved] = next.splice(fromIndex, 1);
		next.splice(toIndex, 0, moved);
		persistOrder(next);
	}
</script>

<div>
	<div class="flex items-baseline gap-3 mb-3">
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted whitespace-nowrap">
			Previews
		</span>
		<div class="flex-1 h-px bg-line self-center"></div>
	</div>

	<div class="bg-surface-1 border border-line rounded-lg shadow-raised p-4 space-y-3">
		<MediaLoaderField
			name="model_preview_add"
			value={pendingValue}
			onChange={handlePendingChange}
			config={{ accept: ['image', 'video', 'audio'] }}
			compact
			compactFullWidth
		/>
		<p class="text-2xs text-fg-subtle">
			The first preview is shown everywhere this model's preview appears.
		</p>

		{#if listError}
			<p class="text-2xs text-danger">{listError}</p>
		{/if}

		{#if loading}
			<div class="flex items-center justify-center py-6">
				<Spinner size="sm" />
			</div>
		{:else if previews.length === 0}
			<p class="text-2xs text-fg-subtle text-center py-2">No previews yet.</p>
		{:else}
			<div class="grid grid-cols-4 gap-2">
				{#each previews as preview, index (preview.id)}
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div
						class="group relative aspect-square rounded border overflow-hidden bg-surface-2 cursor-grab active:cursor-grabbing {index ===
						0
							? 'border-accent'
							: 'border-line'}"
						style={preview.type !== 'image' ? placeholderTint(preview.name || preview.id) : undefined}
						draggable="true"
						on:dragstart={(e) => onTileDragStart(e, preview.id)}
						on:dragover={onTileDragOver}
						on:drop={(e) => onTileDrop(e, preview.id)}
						title={preview.name || preview.type}
					>
						{#if preview.type === 'image'}
							<img
								src="{preview.url}?size=small"
								alt={preview.name || 'Model preview'}
								class="w-full h-full object-cover pointer-events-none"
							/>
						{:else}
							<div class="w-full h-full flex flex-col items-center justify-center gap-1 pointer-events-none">
								<Icon name={preview.type === 'video' ? 'video' : 'audio'} className="w-5 h-5 text-fg-muted" />
								<span class="text-2xs text-fg-subtle px-1 truncate max-w-full">{preview.name || preview.type}</span>
							</div>
						{/if}

						{#if index === 0}
							<span
								class="absolute top-1 left-1 bg-accent text-canvas text-2xs font-mono px-1 rounded pointer-events-none"
							>
								Primary
							</span>
						{/if}

						<div
							class="absolute inset-0 bg-canvas/60 opacity-0 group-hover:opacity-100 transition-opacity duration-100 flex items-center justify-center gap-1"
						>
							<div class="absolute top-1 right-1">
								<IconButton
									icon="trash"
									label="Remove preview"
									size="sm"
									onclick={() => removePreview(preview.id)}
								/>
							</div>
							{#if index !== 0}
								<IconButton
									icon="star"
									label="Make primary"
									size="sm"
									variant="secondary"
									onclick={() => makePrimary(preview.id)}
								/>
							{/if}
							<span class="absolute bottom-1 left-1 text-fg-subtle" title="Drag to reorder">
								<Icon name="grip" className="w-3.5 h-3.5" />
							</span>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
