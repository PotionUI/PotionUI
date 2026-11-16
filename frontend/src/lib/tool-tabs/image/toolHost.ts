import { readable, writable, type Readable } from 'svelte/store';
import type { EditorToolHost } from '$lib/editors/railTools';
import type { Selection } from '$lib/editors/overlays';
import type { PixelSize } from '$lib/editors/types';
import type { MediaToolInputRef } from '$lib/media-tools/types';
import { apply, beginEdit, discardPending, liveEdit, updatePending, type EditStackState, type PendingEdit, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { sliceLast } from './slice/stack';

export interface ImageToolHostDeps {
	getStack(): EditStackState<unknown>;
	setStack(next: EditStackState<unknown>): void;
	getSource(): { size: PixelSize | null; ref: MediaToolInputRef | null };
	render(stepsBeneath?: number): Promise<HTMLCanvasElement>;
	setApply(state: { label: string; enabled: boolean } | null): void;
}

export interface ImageToolHost extends EditorToolHost<unknown> {
	syncPending(pending: PendingEdit<unknown> | null): void;
}

export function createImageToolHost(deps: ImageToolHostDeps): ImageToolHost {
	const pending = writable<PendingEdit<unknown> | null>(null);
	const selection: Readable<Selection | null> = readable(null);
	return {
		editor: 'image',
		get source() {
			return deps.getSource();
		},
		pending: { subscribe: pending.subscribe },
		selection,
		syncPending: (next) => pending.set(next),
		begin: (draft) => deps.setStack(beginEdit(deps.getStack(), draft)),
		update: (patch: Partial<StepDraft<unknown>>) => deps.setStack(updatePending(deps.getStack(), patch)),
		apply: () => deps.setStack(sliceLast(apply(deps.getStack()))),
		discard: () => deps.setStack(discardPending(deps.getStack())),
		live: (draft, match) => deps.setStack(liveEdit(deps.getStack(), draft, match)),
		setApply: (state) => deps.setApply(state),
		render: (stepsBeneath) => deps.render(stepsBeneath)
	};
}
