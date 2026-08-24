import { writable, derived, get, type Writable, type Readable } from 'svelte/store';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import { modelLibraryStore } from '$lib/stores/modelLibrary';
import { modelDisplayName } from '$lib/utils/modelDisplay';
import {
	filesWithPreview,
	previewItemsAsFiles,
	type ModelPreviewMedia,
	type ModelPreviewMediaItem
} from '$lib/utils/modelPreview';
import type { ModelAvailabilityResponse } from '$lib/types/models';

export type ModelDetailsScope = 'library' | 'admin';

/**
 * What a given scope may see and do. Driven only by `scope` — never derived
 * from inspecting a loaded model — so a wrapper's rendering decisions are
 * fixed before any network response exists.
 */
export interface ModelDetailsCapabilities {
	scope: ModelDetailsScope;
	/** Description, trigger words, tags: the admin curation surface. */
	canEditMetadata: boolean;
	/** Prompting guidance is admin-only data — it isn't in a library payload at all. */
	canEditPromptingGuidance: boolean;
	/** Favorite + add-to-collection: the signed-in user's own library actions. */
	canManageLibrary: boolean;
	/** Filename/path/hash/size/indexed-at — operational detail, admin only. */
	canViewOperationalDetails: boolean;
	/** Per-backend availability. The endpoint itself 403s for non-admins. */
	canViewAvailability: boolean;
	/** The admin's own multi-item preview upload/reorder gallery. */
	canManagePreviewGallery: boolean;
	/** Assign this model directly to users/groups. The endpoints 403 for non-admins. */
	canManageAssignments: boolean;
}

export function resolveModelDetailsCapabilities(scope: ModelDetailsScope): ModelDetailsCapabilities {
	if (scope === 'admin') {
		return {
			scope,
			canEditMetadata: true,
			canEditPromptingGuidance: true,
			canManageLibrary: false,
			canViewOperationalDetails: true,
			canViewAvailability: true,
			canManagePreviewGallery: true,
			canManageAssignments: true
		};
	}
	return {
		scope,
		canEditMetadata: false,
		canEditPromptingGuidance: false,
		canManageLibrary: true,
		canViewOperationalDetails: false,
		canViewAvailability: false,
		canManagePreviewGallery: false,
		canManageAssignments: false
	};
}

/** The library-safe shape. Every field a generating user is allowed to see. */
export interface ModelSummary {
	id: string;
	filename: string;
	name?: string | null;
	model_type: string;
	created_at?: string | null;
	description?: string | null;
	custom_name?: string | null;
	is_favorite?: boolean;
	preview_media?: ModelPreviewMedia | null;
	files?: any[];
	tags?: Array<{ id: string; name: string }>;
	/** Admin-set SHARED attribute values (trigger words live here, under the
	 *  `triggers` key - see constants/modelMetadata.ts and ModelAttributesCard). */
	model_metadata?: Record<string, unknown>;
	/** The requesting user's own per-user attribute overlay. */
	user_model_metadata?: Record<string, unknown>;
}

/** Adds the operational block. Only `toAdminModelDetails` ever constructs one of these. */
export interface AdminModelDetails extends ModelSummary {
	file_path: string | null;
	file_size: number | null;
	sha256: string | null;
	indexed_at: string | null;
	updated_at: string | null;
	prompting_guidance: string | null;
	is_directory: boolean;
}

/**
 * Picks the library-safe fields off a raw `/api/models/{id}` payload. This is
 * the field allowlist that keeps `createLibraryModelDetailsController` from
 * ever holding an operational field, regardless of what the response body
 * happens to contain — the server already omits them for a non-admin caller
 * (`admin=False` in `ModelRecord.to_dict`), this is the redundant client-side
 * half of that guarantee.
 */
export function toModelSummary(raw: any): ModelSummary {
	return {
		id: raw.id,
		filename: raw.filename,
		name: raw.name ?? null,
		model_type: raw.model_type,
		created_at: raw.created_at ?? null,
		description: raw.description ?? null,
		custom_name: raw.custom_name ?? null,
		is_favorite: !!raw.is_favorite,
		preview_media: raw.preview_media ?? null,
		files: raw.files ?? [],
		tags: raw.tags ?? [],
		model_metadata: raw.model_metadata ?? undefined,
		user_model_metadata: raw.user_model_metadata ?? undefined
	};
}

/** Only ever called from `createAdminModelDetailsController`. */
export function toAdminModelDetails(raw: any): AdminModelDetails {
	return {
		...toModelSummary(raw),
		file_path: raw.file_path ?? null,
		file_size: raw.file_size ?? null,
		sha256: raw.sha256 ?? null,
		indexed_at: raw.indexed_at ?? null,
		updated_at: raw.updated_at ?? null,
		prompting_guidance: raw.prompting_guidance ?? null,
		is_directory: !!raw.is_directory
	};
}

function byDisplayOrder(a: any, b: any): number {
	return (a.display_order ?? 0) - (b.display_order ?? 0);
}

function isViewableMediaFile(file: any): boolean {
	return file.file_type === 'image' || file.file_type === 'thumbnail' || file.file_type === 'video';
}

function toTagList(raw: any): Array<{ id: string; name: string }> {
	return (raw?.tags ?? []).map((tag: any) => ({ id: tag.id, name: tag.name }));
}

export interface LibraryModelDetailsController {
	capabilities: ModelDetailsCapabilities;
	loading: Writable<boolean>;
	model: Writable<ModelSummary | null>;
	currentImageIndex: Writable<number>;
	imageFiles: Readable<any[]>;
	displayName: Readable<string>;
	selectedTags: Writable<Array<{ id: string; name: string }>>;
	selectedTagIds: Writable<string[]>;
	isFavorite: Writable<boolean>;

	load(modelId: string): Promise<void>;
	reset(): void;
	prevImage(): void;
	nextImage(): void;
	handleKeydownNav(event: KeyboardEvent): void;
	rename(name: string): Promise<void>;
	toggleFavorite(): Promise<void>;
	addToCollection(collectionId: string): Promise<void>;
}

/**
 * The user-facing / library scope. Its `model` store can only ever hold a
 * `ModelSummary` — `toModelSummary` is the only mapper this factory calls,
 * and there is no method here that reads or forwards an operational field.
 * A wrapper built on this controller has nothing to leak: the fields don't
 * exist on the object it holds, not merely fields it chooses not to render.
 */
export function createLibraryModelDetailsController(): LibraryModelDetailsController {
	const capabilities = resolveModelDetailsCapabilities('library');

	const loading = writable(true);
	const model: Writable<ModelSummary | null> = writable(null);
	const previews: Writable<ModelPreviewMediaItem[]> = writable([]);
	const currentImageIndex = writable(0);
	const selectedTags = writable<Array<{ id: string; name: string }>>([]);
	const selectedTagIds = writable<string[]>([]);
	const isFavorite = writable(false);

	const imageFiles: Readable<any[]> = derived([model, previews], ([$model, $previews]) => {
		if (!$model) return [];
		const providerFiles = $model.files ?? [];
		return [...previewItemsAsFiles($previews, { allowVideo: true }), ...providerFiles]
			.filter(isViewableMediaFile)
			.sort(byDisplayOrder);
	});

	const displayName: Readable<string> = derived(model, ($model) => modelDisplayName($model));

	async function loadModel(modelId: string, showLoading = true) {
		if (showLoading) loading.set(true);
		try {
			const response = await api.getModelById(modelId, true);
			if (response.success && response.data) {
				const raw = response.data.model;
				model.set(toModelSummary(raw));
				isFavorite.set(!!raw.is_favorite);
				const tags = toTagList(raw);
				selectedTags.set(tags);
				selectedTagIds.set(tags.map((t) => t.id));
			}
		} catch (error) {
			logger.error('Failed to load model:', error);
		} finally {
			if (showLoading) loading.set(false);
		}
	}

	async function loadPreviews(modelId: string) {
		previews.set([]);
		try {
			const response = await api.listModelPreviews(modelId);
			if (response.success && response.data) {
				previews.set(response.data.previews);
			}
		} catch (error) {
			// Non-fatal: falls back to provider-supplied files only.
			logger.error('Failed to load model previews:', getErrorMessage(error));
		}
	}

	async function load(modelId: string) {
		currentImageIndex.set(0);
		await Promise.all([loadModel(modelId), loadPreviews(modelId)]);
	}

	function reset() {
		model.set(null);
		previews.set([]);
	}

	function prevImage() {
		currentImageIndex.update((i) => (i > 0 ? i - 1 : i));
	}

	function nextImage() {
		const total = get(imageFiles).length;
		currentImageIndex.update((i) => (i < total - 1 ? i + 1 : i));
	}

	function handleKeydownNav(event: KeyboardEvent) {
		if (event.key === 'ArrowLeft') prevImage();
		else if (event.key === 'ArrowRight') nextImage();
	}

	async function rename(name: string) {
		const current = get(model);
		if (!current?.id) return;
		try {
			const response = await api.setModelLibraryName(current.id, name || null);
			if (response.success) {
				await loadModel(current.id, false);
			}
		} catch (error) {
			logger.error('Failed to save library name:', getErrorMessage(error));
		}
	}

	async function toggleFavorite() {
		const current = get(model);
		if (!current?.id) return;
		const next = !get(isFavorite);
		isFavorite.set(next);
		try {
			const response = await api.setModelFavorite(current.id, next);
			if (!response.success) throw new Error('Favorite update failed');
			model.update((m) => (m ? { ...m, is_favorite: next } : m));
		} catch (error) {
			isFavorite.set(!next);
			logger.error('Failed to toggle favorite:', getErrorMessage(error));
		}
	}

	async function addToCollection(collectionId: string) {
		const current = get(model);
		if (!current?.id) return;
		try {
			await modelLibraryStore.addMembers(collectionId, [current.id]);
		} catch (error) {
			logger.error('Failed to add model to collection:', getErrorMessage(error));
		}
	}

	return {
		capabilities,
		loading,
		model,
		currentImageIndex,
		imageFiles,
		displayName,
		selectedTags,
		selectedTagIds,
		isFavorite,
		load,
		reset,
		prevImage,
		nextImage,
		handleKeydownNav,
		rename,
		toggleFavorite,
		addToCollection
	};
}

export interface AdminModelDetailsController {
	capabilities: ModelDetailsCapabilities;
	loading: Writable<boolean>;
	model: Writable<AdminModelDetails | null>;
	currentImageIndex: Writable<number>;
	imageFiles: Readable<any[]>;
	displayName: Readable<string>;
	selectedTags: Writable<Array<{ id: string; name: string }>>;
	selectedTagIds: Writable<string[]>;
	availability: Writable<ModelAvailabilityResponse | null>;
	availabilityLoading: Writable<boolean>;
	savingDescription: Writable<boolean>;
	savingPromptingGuidance: Writable<boolean>;

	load(modelId: string): Promise<void>;
	reset(): void;
	prevImage(): void;
	nextImage(): void;
	handleKeydownNav(event: KeyboardEvent): void;
	rename(name: string): Promise<void>;
	saveDescription(value: string): Promise<void>;
	savePromptingGuidance(value: string): Promise<void>;
	updateTags(tagIds: string[]): Promise<void>;
	handlePrimaryPreviewChange(preview: ModelPreviewMedia | null): void;
}

/**
 * The admin scope. Only ever opened from the admin Models tab. Its `model`
 * store holds an `AdminModelDetails` — the operational block plus every
 * editing action — none of which the library controller above exposes.
 */
export function createAdminModelDetailsController(): AdminModelDetailsController {
	const capabilities = resolveModelDetailsCapabilities('admin');

	const loading = writable(true);
	const model: Writable<AdminModelDetails | null> = writable(null);
	const currentImageIndex = writable(0);
	const selectedTags = writable<Array<{ id: string; name: string }>>([]);
	const selectedTagIds = writable<string[]>([]);
	const availability: Writable<ModelAvailabilityResponse | null> = writable(null);
	const availabilityLoading = writable(false);
	const savingDescription = writable(false);
	const savingPromptingGuidance = writable(false);

	const imageFiles: Readable<any[]> = derived(model, ($model) => {
		if (!$model) return [];
		return filesWithPreview($model, { allowVideo: true }).filter(isViewableMediaFile).sort(byDisplayOrder);
	});

	const displayName: Readable<string> = derived(model, ($model) => modelDisplayName($model));

	async function loadModel(modelId: string, showLoading = true) {
		if (showLoading) loading.set(true);
		try {
			const response = await api.getModelById(modelId, true);
			if (response.success && response.data) {
				const raw = response.data.model;
				model.set(toAdminModelDetails(raw));
				const tags = toTagList(raw);
				selectedTags.set(tags);
				selectedTagIds.set(tags.map((t) => t.id));
			}
		} catch (error) {
			logger.error('Failed to load model:', error);
		} finally {
			if (showLoading) loading.set(false);
		}
	}

	async function loadAvailability(modelId: string) {
		availabilityLoading.set(true);
		try {
			const response = await api.getModelAvailability(modelId);
			if (response.success && response.data) {
				availability.set(response.data);
			}
		} catch (error) {
			logger.error('Failed to get model availability:', getErrorMessage(error));
		} finally {
			availabilityLoading.set(false);
		}
	}

	async function load(modelId: string) {
		currentImageIndex.set(0);
		await Promise.all([loadModel(modelId), loadAvailability(modelId)]);
	}

	function reset() {
		availability.set(null);
	}

	function prevImage() {
		currentImageIndex.update((i) => (i > 0 ? i - 1 : i));
	}

	function nextImage() {
		const total = get(imageFiles).length;
		currentImageIndex.update((i) => (i < total - 1 ? i + 1 : i));
	}

	function handleKeydownNav(event: KeyboardEvent) {
		if (event.key === 'ArrowLeft') prevImage();
		else if (event.key === 'ArrowRight') nextImage();
	}

	async function rename(name: string) {
		const current = get(model);
		if (!current?.id) return;
		try {
			const response = await api.setModelLibraryName(current.id, name || null);
			if (response.success) {
				await loadModel(current.id, false);
			}
		} catch (error) {
			logger.error('Failed to save library name:', getErrorMessage(error));
		}
	}

	async function saveDescription(value: string) {
		const current = get(model);
		if (!current?.id) return;
		savingDescription.set(true);
		try {
			const response = await api.updateModelDescription(current.id, value);
			if (response.success) {
				model.update((m) => (m ? { ...m, description: value } : m));
			}
		} catch (error) {
			logger.error('Failed to save description:', error);
		} finally {
			savingDescription.set(false);
		}
	}

	async function savePromptingGuidance(value: string) {
		const current = get(model);
		if (!current?.id) return;
		savingPromptingGuidance.set(true);
		try {
			const response = await api.updateModelPromptingGuidance(current.id, value);
			if (response.success) {
				model.update((m) => (m ? { ...m, prompting_guidance: value } : m));
			}
		} catch (error) {
			logger.error('Failed to save prompting guidance:', error);
		} finally {
			savingPromptingGuidance.set(false);
		}
	}

	async function updateTags(tagIds: string[]) {
		const current = get(model);
		if (!current?.id) return;
		try {
			const response = await api.updateModelTags(current.id, tagIds);
			if (response.success && response.data) {
				const tags = toTagList(response.data.model);
				selectedTagIds.set(tagIds);
				selectedTags.set(tags);
			}
		} catch (error) {
			logger.error('Failed to update tags:', error);
		}
	}

	function handlePrimaryPreviewChange(preview: ModelPreviewMedia | null) {
		model.update((m) => (m ? { ...m, preview_media: preview } : m));
	}

	return {
		capabilities,
		loading,
		model,
		currentImageIndex,
		imageFiles,
		displayName,
		selectedTags,
		selectedTagIds,
		availability,
		availabilityLoading,
		savingDescription,
		savingPromptingGuidance,
		load,
		reset,
		prevImage,
		nextImage,
		handleKeydownNav,
		rename,
		saveDescription,
		savePromptingGuidance,
		updateTags,
		handlePrimaryPreviewChange
	};
}
