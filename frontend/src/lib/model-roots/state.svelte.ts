import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api';
import type {
	CreateModelRootPayload,
	ModelLayoutSummary,
	ModelRoot,
	ModelRootBinding,
	ModelRootBrowseResult,
	ModelRootDetection,
	ModelRootsOverview,
	UpdateModelRootPayload
} from '$lib/services/api/models';
import { modelRootsErrorMessage } from './errors';

export class ModelRootsState {
	overview = $state<ModelRootsOverview | null>(null);
	loading = $state(true);
	error = $state<string | null>(null);
	mutating = $state(false);

	async load(): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			const response = await api.getModelRoots();
			if (response.success && response.data) {
				this.overview = response.data;
			} else {
				this.error = response.message ?? 'Failed to load model roots.';
			}
		} catch (e) {
			logger.error('Failed to load model roots:', e);
			this.error = modelRootsErrorMessage(e, 'Failed to load model roots.');
		} finally {
			this.loading = false;
		}
	}

	async refresh(): Promise<void> {
		try {
			const response = await api.getModelRoots();
			if (response.success && response.data) {
				this.overview = response.data;
			}
		} catch (e) {
			logger.error('Failed to refresh model roots:', e);
		}
	}

	get roots(): ModelRoot[] {
		return this.overview?.roots ?? [];
	}

	rootById(rootId: string): ModelRoot | undefined {
		return this.roots.find((r) => r.id === rootId);
	}

	async create(payload: CreateModelRootPayload): Promise<ModelRoot | null> {
		this.mutating = true;
		this.error = null;
		try {
			const response = await api.createModelRoot(payload);
			if (response.success && response.data) {
				await this.refresh();
				return response.data;
			}
			this.error = response.message ?? 'Failed to add the folder.';
			return null;
		} catch (e) {
			logger.error('Failed to create model root:', e);
			this.error = modelRootsErrorMessage(e, 'Failed to add the folder.');
			throw e;
		} finally {
			this.mutating = false;
		}
	}

	async update(rootId: string, payload: UpdateModelRootPayload): Promise<ModelRoot | null> {
		this.mutating = true;
		this.error = null;
		try {
			const response = await api.updateModelRoot(rootId, payload);
			if (response.success && response.data) {
				await this.refresh();
				return response.data;
			}
			this.error = response.message ?? 'Failed to update the folder.';
			return null;
		} catch (e) {
			logger.error('Failed to update model root:', e);
			this.error = modelRootsErrorMessage(e, 'Failed to update the folder.');
			throw e;
		} finally {
			this.mutating = false;
		}
	}

	async setBindingScan(
		rootId: string,
		binding: Pick<ModelRootBinding, 'model_type' | 'subdir'>,
		scanHeaders: boolean
	): Promise<ModelRoot | null> {
		try {
			const response = await api.setModelRootBindingScan(rootId, {
				model_type: binding.model_type,
				subdir: binding.subdir,
				scan_headers: scanHeaders
			});
			if (response.success && response.data) {
				await this.refresh();
				return response.data;
			}
			this.error = response.message ?? 'Failed to change header detection.';
			return null;
		} catch (e) {
			logger.error('Failed to change binding header detection:', e);
			this.error = modelRootsErrorMessage(e, 'Failed to change header detection.');
			throw e;
		}
	}

	async remove(rootId: string): Promise<boolean> {
		this.mutating = true;
		this.error = null;
		try {
			const response = await api.deleteModelRoot(rootId);
			if (response.success) {
				await this.refresh();
				return true;
			}
			this.error = response.message ?? 'Failed to delete the folder.';
			return false;
		} catch (e) {
			logger.error('Failed to delete model root:', e);
			this.error = modelRootsErrorMessage(e, 'Failed to delete the folder.');
			throw e;
		} finally {
			this.mutating = false;
		}
	}

	async reorder(rootIds: string[], modelType?: string): Promise<boolean> {
		this.mutating = true;
		this.error = null;
		try {
			const response = await api.reorderModelRoots(rootIds, modelType);
			if (response.success && response.data) {
				this.overview = response.data;
				return true;
			}
			this.error = response.message ?? 'Failed to reorder folders.';
			return false;
		} catch (e) {
			logger.error('Failed to reorder model roots:', e);
			this.error = modelRootsErrorMessage(e, 'Failed to reorder folders.');
			throw e;
		} finally {
			this.mutating = false;
		}
	}

	async setWrite(rootId: string, modelType?: string, subdir?: string): Promise<ModelRoot | null> {
		this.mutating = true;
		this.error = null;
		try {
			const response = await api.setModelRootWrite(rootId, modelType, subdir);
			if (response.success && response.data) {
				await this.refresh();
				return response.data;
			}
			this.error = response.message ?? 'Failed to set the write folder.';
			return null;
		} catch (e) {
			logger.error('Failed to set model root write target:', e);
			this.error = modelRootsErrorMessage(e, 'Failed to set the write folder.');
			throw e;
		} finally {
			this.mutating = false;
		}
	}

	async probe(rootId: string): Promise<ModelRoot | null> {
		try {
			const response = await api.probeModelRoot(rootId);
			if (response.success && response.data) {
				await this.refresh();
				return response.data;
			}
			return null;
		} catch (e) {
			logger.error('Failed to probe model root:', e);
			return null;
		}
	}
}

export async function detectModelRoot(path: string, profile?: string): Promise<{
	detection: ModelRootDetection | null;
	error: string | null;
}> {
	try {
		const response = await api.detectModelRoot(path, profile);
		if (response.success && response.data) {
			return { detection: response.data, error: null };
		}
		return { detection: null, error: response.message ?? 'Failed to inspect that folder.' };
	} catch (e) {
		logger.error('Failed to detect model root layout:', e);
		return { detection: null, error: modelRootsErrorMessage(e, 'Failed to inspect that folder.') };
	}
}

export async function browseModelRoot(path: string, sub?: string): Promise<{
	listing: ModelRootBrowseResult | null;
	error: string | null;
}> {
	try {
		const response = await api.browseModelRoot(path, sub);
		if (response.success && response.data) {
			return { listing: response.data, error: null };
		}
		return { listing: null, error: response.message ?? 'Could not list that folder.' };
	} catch (e) {
		logger.error('Failed to browse model root:', e);
		return { listing: null, error: modelRootsErrorMessage(e, 'Could not list that folder.') };
	}
}

export async function listModelLayouts(): Promise<ModelLayoutSummary[]> {
	try {
		const response = await api.listModelLayouts();
		return response.layouts ?? [];
	} catch (e) {
		logger.error('Failed to load model layouts:', e);
		return [];
	}
}

export function serverPathPlaceholder(pathStyle: 'windows' | 'posix' | undefined): string {
	return pathStyle === 'windows' ? 'D:\\ComfyUI\\models' : '/mnt/storage/ComfyUI/models';
}

export function bindingLabel(binding: { model_type: string; folder: string }): string {
	return binding.folder || binding.model_type;
}
