import { goto } from '$app/navigation';
import { hasOpenDismissLayer } from '$lib/actions/layerStack';
import { openLibraryResultModal, type LandingSource } from '$lib/library/landing';
import { removePendingFresh, setPendingFresh } from '$lib/library/freshHandoff';
import { libraryItemFromEdit } from '$lib/library/libraryItemEdit';
import type { EditedMediaItem } from '$lib/services/api/media';
import { toasts } from '$lib/stores/toast';
import { isEditableTarget } from '$lib/utils/editableTarget';

export interface LandingEnv {
	overlayOpen: boolean;
	typing: boolean;
	samePage: boolean;
}

export type Landing =
	| { kind: 'none' }
	| { kind: 'modal' }
	| { kind: 'toast-open' }
	| { kind: 'toast-split' };

export function decideLanding(count: number, env: LandingEnv): Landing {
	if (count === 0) return { kind: 'none' };
	if (count > 1) return { kind: 'toast-split' };
	if (env.overlayOpen || env.typing || !env.samePage) return { kind: 'toast-open' };
	return { kind: 'modal' };
}

export function readLandingEnv(startPath: string): LandingEnv {
	if (typeof document === 'undefined') return { overlayOpen: false, typing: false, samePage: true };
	return {
		overlayOpen: hasOpenDismissLayer(),
		typing: isEditableTarget(document.activeElement),
		samePage: window.location.pathname === startPath
	};
}

export function pluralNoun(items: readonly EditedMediaItem[]): string {
	const types = new Set(items.map((item) => item.media_type));
	if (types.size !== 1) return 'items';
	const [type] = [...types];
	if (type === 'video') return 'clips';
	if (type === 'image') return 'images';
	if (type === 'audio') return 'tracks';
	return 'items';
}

export interface LandOptions {
	source: LandingSource | null;
	startPath: string;
	env?: LandingEnv;
}

export function landOnHistoryResult(items: readonly EditedMediaItem[], options: LandOptions): Landing {
	const landing = decideLanding(items.length, options.env ?? readLandingEnv(options.startPath));
	if (landing.kind === 'modal') {
		openLibraryResultModal(libraryItemFromEdit(items[0]), options.source);
	} else if (landing.kind === 'toast-open') {
		const item = items[0];
		setPendingFresh([item.id]);
		toasts.show('success', 'Saved to Library', {
			title: 'Saved to Library',
			action: {
				label: 'Open',
				onClick: () => {
					removePendingFresh(item.id);
					openLibraryResultModal(libraryItemFromEdit(item), options.source);
				}
			}
		});
	} else if (landing.kind === 'toast-split') {
		setPendingFresh(items.map((item) => item.id));
		toasts.show('success', `${items.length} ${pluralNoun(items)} saved to Library, tagged New`, {
			title: 'Saved to Library',
			action: { label: 'Open Library', onClick: () => void goto('/library') }
		});
	}
	return landing;
}
