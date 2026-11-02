import { createImageEditorApi, type PluginImageEditorApi } from '$lib/components/imageEditor/registries';
import { api } from '$lib/services/api/index';
import { toasts } from '$lib/stores/toast';
import { tabsStore } from '$lib/stores/tabs';
import type { MediaRef } from '$lib/types/tabs';
import { lastToolFor } from './lastTool';

function canvasToPng(canvas: HTMLCanvasElement): Promise<Blob | null> {
	return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), 'image/png'));
}

function pngName(name: string): string {
	const stem = name.replace(/\.[a-z0-9]+$/i, '').trim() || 'Image';
	return `${stem}.png`;
}

export async function openCanvasInImageEditor(canvas: HTMLCanvasElement, name: string): Promise<boolean> {
	const blob = await canvasToPng(canvas);
	if (!blob) {
		toasts.error('This image could not be opened in the Image editor.');
		return false;
	}
	const filename = pngName(name);
	const response = await api.uploadMedia(new File([blob], filename, { type: 'image/png' }), 'derived_artifact');
	if (!response.success || !response.data) {
		toasts.error('This image could not be opened in the Image editor.');
		return false;
	}
	const upload = response.data;
	const source: MediaRef = {
		path: upload.path,
		relative_path: upload.relative_path,
		url: upload.url,
		name: filename,
		type: 'image'
	};
	const id = tabsStore.openToolTab('image', source, { activeTool: lastToolFor('image') });
	tabsStore.setActiveTab(id);
	return true;
}

export function createHostImageEditorApi(): PluginImageEditorApi {
	const editor = createImageEditorApi();
	return {
		...editor,
		openImage(canvas, name) {
			if (editor.openImage(canvas, name)) return true;
			void openCanvasInImageEditor(canvas, name);
			return true;
		}
	};
}
