import { api } from '$lib/services/api/index';
import { blockedUploadMessage } from '$lib/plans/uploadLimit';
import { isPerItemError, refusalMessage } from '$lib/plans/refusal';
import { getErrorMessage } from '$lib/utils/logger';
import type { MediaAsset } from './types';

export type FileKind = 'image' | 'video';
export type UploadStatus = 'queued' | 'uploading' | 'done' | 'error';

export interface UploadItem {
	id: number;
	name: string;
	status: UploadStatus;
	error: string | null;
}

export interface UploadResponse {
	success: boolean;
	message?: string;
	error?: string;
	data?: { id?: string | null; width?: number | null; height?: number | null };
}

export interface UploadOptions {
	mediaType: string | null | undefined;
	upload?: (file: File) => Promise<UploadResponse>;
	blocked?: (file: File) => string | null;
	onchange: (items: UploadItem[]) => void;
	onassets: (assets: MediaAsset[]) => void;
	concurrency?: number;
}

export function kindOfFile(file: Pick<File, 'type' | 'name'>): FileKind | null {
	const type = (file.type || '').toLowerCase();
	if (type.startsWith('image/')) return 'image';
	if (type.startsWith('video/')) return 'video';
	return null;
}

export function acceptFor(mediaType: string | null | undefined): string {
	return mediaType === 'image' ? 'image/*' : mediaType === 'video' ? 'video/*' : 'image/*,video/*';
}

function rejection(file: File, kind: FileKind | null, want: string | null): string | null {
	if (!kind) return 'Only images and videos can be added to a files step.';
	if (want && kind !== want) return `This step holds ${want === 'image' ? 'images' : 'videos'}; this file is ${kind === 'image' ? 'an image' : 'a video'}.`;
	return null;
}

export function filesFromClipboard(data: Pick<DataTransfer, 'files' | 'items'> | null | undefined): File[] {
	if (!data) return [];
	const direct = Array.from(data.files ?? []);
	if (direct.length > 0) return direct;
	const found: File[] = [];
	for (const item of Array.from(data.items ?? [])) {
		if (item.kind !== 'file') continue;
		const file = item.getAsFile();
		if (file) found.push(file);
	}
	return found;
}

export function pastedName(file: File, index: number): string {
	if (file.name && file.name !== 'image.png') return file.name;
	const extension = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
	return `pasted-${Date.now()}-${index + 1}.${extension}`;
}

export function nameClipboardFiles(files: File[]): File[] {
	return files.map((file, index) => {
		const name = pastedName(file, index);
		return name === file.name ? file : new File([file], name, { type: file.type });
	});
}

async function defaultUpload(file: File): Promise<UploadResponse> {
	return api.uploadLibraryMedia(file);
}

function failure(error: unknown): string {
	return refusalMessage(error) ?? getErrorMessage(error) ?? 'The upload failed.';
}

export async function uploadIntoFilesStep(files: File[], options: UploadOptions): Promise<{ assets: MediaAsset[]; failed: number }> {
	const upload = options.upload ?? defaultUpload;
	const blocked = options.blocked ?? ((file: File) => blockedUploadMessage([file]));
	const items: UploadItem[] = files.map((file, id) => ({ id, name: file.name, status: 'queued', error: null }));
	const kinds = files.map((file) => kindOfFile(file));
	let want: string | null = options.mediaType ?? null;
	if (!want) want = kinds.find((kind) => kind) ?? null;

	const results: Array<MediaAsset | null | undefined> = files.map(() => undefined);
	const emit = () => options.onchange(items.map((item) => ({ ...item })));
	let delivered = 0;
	let stop = false;

	const settle = (index: number, asset: MediaAsset | null, error: string | null) => {
		results[index] = asset;
		items[index] = { ...items[index], status: asset ? 'done' : 'error', error };
		const ready: MediaAsset[] = [];
		while (delivered < results.length && results[delivered] !== undefined) {
			const next = results[delivered];
			if (next) ready.push(next);
			delivered += 1;
		}
		if (ready.length > 0) options.onassets(ready);
		emit();
	};

	const queue = files.map((_, index) => index);
	const work = async () => {
		for (let index = queue.shift(); index !== undefined; index = queue.shift()) {
			const file = files[index];
			const refused = rejection(file, kinds[index], want) ?? blocked(file);
			if (refused || stop) {
				settle(index, null, refused ?? 'Skipped after an earlier error.');
				continue;
			}
			items[index] = { ...items[index], status: 'uploading' };
			emit();
			try {
				const response = await upload(file);
				const id = response.success ? response.data?.id : null;
				if (!id) {
					settle(index, null, response.message || response.error || 'The upload did not return a library item.');
					continue;
				}
				settle(
					index,
					{
						kind: 'library',
						item_id: id,
						media_type: kinds[index],
						original_filename: file.name,
						preview_url: null,
						width: response.data?.width ?? null,
						height: response.data?.height ?? null
					},
					null
				);
			} catch (error) {
				if (refusalMessage(error) && !isPerItemError(error)) stop = true;
				settle(index, null, failure(error));
			}
		}
	};

	emit();
	await Promise.all(Array.from({ length: Math.max(1, Math.min(options.concurrency ?? 3, files.length)) }, work));
	const assets = results.filter((asset): asset is MediaAsset => !!asset);
	return { assets, failed: files.length - assets.length };
}

export function appendAssets(existing: MediaAsset[] | null | undefined, added: MediaAsset[]): MediaAsset[] {
	const keys = new Set((existing ?? []).map((asset) => `${asset.kind}:${asset.item_id ?? `${asset.generation_id}:${asset.file_id}`}`));
	const fresh = added.filter((asset) => {
		const key = `${asset.kind}:${asset.item_id ?? `${asset.generation_id}:${asset.file_id}`}`;
		if (keys.has(key)) return false;
		keys.add(key);
		return true;
	});
	return [...(existing ?? []), ...fresh];
}

export function uploadSummary(items: UploadItem[]) {
	const done = items.filter((item) => item.status === 'done').length;
	const failed = items.filter((item) => item.status === 'error');
	return { total: items.length, done, failed, active: items.some((item) => item.status === 'queued' || item.status === 'uploading') };
}
