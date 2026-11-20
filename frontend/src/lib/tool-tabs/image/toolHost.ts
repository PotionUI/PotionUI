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
	writeLive(next: EditStackState<unknown>, options?: { hold?: boolean }): void;
	settleLive(): void;
}

export interface ImageToolHost extends EditorToolHost<unknown> {
	stack: Readable<EditStackState<unknown>>;
	syncPending(pending: PendingEdit<unknown> | null): void;
	syncStack(stack: EditStackState<unknown>): void;
	edit(next: EditStackState<unknown>, options?: { hold?: boolean }): void;
	settle(): void;
	current(): EditStackState<unknown>;
}

export function createImageToolHost(deps: ImageToolHostDeps): ImageToolHost {
	const pending = writable<PendingEdit<unknown> | null>(null);
	const stack = writable<EditStackState<unknown>>(deps.getStack());
	const selection: Readable<Selection | null> = readable(null);
	return {
		editor: 'image',
		get source() {
			return deps.getSource();
		},
		pending: { subscribe: pending.subscribe },
		selection,
		stack: { subscribe: stack.subscribe },
		syncPending: (next) => pending.set(next),
		syncStack: (next) => stack.set(next),
		edit: (next, options) => deps.writeLive(next, options),
		current: () => deps.getStack(),
		settle: () => deps.settleLive(),
		begin: (draft) => deps.setStack(beginEdit(deps.getStack(), draft)),
		update: (patch: Partial<StepDraft<unknown>>) => deps.setStack(updatePending(deps.getStack(), patch)),
		apply: () => deps.setStack(sliceLast(apply(deps.getStack()))),
		discard: () => deps.setStack(discardPending(deps.getStack())),
		live: (draft, match) => deps.setStack(liveEdit(deps.getStack(), draft, match)),
		setApply: (state) => deps.setApply(state),
		render: (stepsBeneath) => deps.render(stepsBeneath)
	};
}
