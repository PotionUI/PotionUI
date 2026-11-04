import { unmount } from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import { toasts } from '$lib/stores/toast';
import type { PickedItem } from './model';

export interface Mountable {
	mount: (target: HTMLElement, props: Record<string, any>) => any;
	update: (handle: any, props: Record<string, any>) => void;
	unmount: (handle: any) => void;
}

export type OverlayRegistry = Record<string, Mountable | undefined>;

interface LazyHandle {
	inner: any;
	props: Record<string, any>;
	destroyed: boolean;
}

function entryFor(component: any): Mountable {
	return {
		mount: (target, props) => createClassComponent({ component, target, props }),
		update: (instance, props) => instance?.$set?.(props),
		unmount: (instance) => {
			if (typeof instance?.$destroy === 'function') instance.$destroy();
			else unmount(instance);
		}
	};
}

function lazyEntry(name: string, load: () => Promise<{ default: any }>): Mountable {
	let real: Mountable | null = null;
	let loading: Promise<Mountable> | null = null;
	const resolve = () => {
		loading ??= load().then(
			(module) => (real = entryFor(module.default)),
			(error) => {
				loading = null;
				throw error;
			}
		);
		return loading;
	};

	return {
		mount: (target, props) => {
			const handle: LazyHandle = { inner: null, props, destroyed: false };
			resolve()
				.then((component) => {
					if (!handle.destroyed) handle.inner = component.mount(target, handle.props);
				})
				.catch((error) => console.error(`Failed to load component '${name}'`, error));
			return handle;
		},
		update: (handle: LazyHandle, props) => {
			if (!handle) return;
			handle.props = { ...handle.props, ...props };
			if (handle.inner) real?.update(handle.inner, props);
		},
		unmount: (handle: LazyHandle) => {
			if (!handle) return;
			handle.destroyed = true;
			if (handle.inner) real?.unmount(handle.inner);
			handle.inner = null;
		}
	};
}

export function createOverlayRegistry(): OverlayRegistry {
	return {
		GenerationDetailsModal: lazyEntry('GenerationDetailsModal', () => import('$lib/components/modals/GenerationDetailsModal.svelte')),
		MediaPreviewModal: lazyEntry('MediaPreviewModal', () => import('$lib/components/modals/MediaPreviewModal.svelte')),
		GenerationHistoryModal: lazyEntry('GenerationHistoryModal', () => import('$lib/components/modals/GenerationHistoryModal.svelte')),
		UploadLibraryModal: lazyEntry('UploadLibraryModal', () => import('$lib/components/modals/UploadLibraryModal.svelte'))
	};
}

export const coreOverlays: OverlayRegistry = createOverlayRegistry();

const PICKERS = {
	history: { name: 'GenerationHistoryModal', title: 'Choose files from History' },
	library: { name: 'UploadLibraryModal', title: 'Choose files from the Library' }
};

export interface MediaPickerOptions {
	selectedKeys: string[];
	mediaTypes?: string[];
	onConfirm: (keys: string[], items: PickedItem[]) => void;
}

export function openMediaPicker(kind: keyof typeof PICKERS, { selectedKeys, mediaTypes, onConfirm }: MediaPickerOptions, registry: OverlayRegistry = coreOverlays): void {
	const picker = PICKERS[kind];
	const entry = registry[picker.name];
	if (!entry) {
		toasts.show('error', 'The media picker is not available.');
		return;
	}
	const el = document.body.appendChild(document.createElement('div'));
	let handle: any = null;
	const close = () => {
		if (handle) entry.unmount(handle);
		handle = null;
		el.remove();
	};
	handle = entry.mount(el, {
		isOpen: true,
		multiple: true,
		title: picker.title,
		selectedKeys,
		selectableMediaTypes: mediaTypes,
		onConfirm: (keys: string[], items: PickedItem[]) => onConfirm(keys, items),
		onClose: () => setTimeout(close, 0)
	});
}
