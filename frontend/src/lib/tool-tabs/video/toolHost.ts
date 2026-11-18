import { readable, writable, type Readable } from 'svelte/store';
import type { EditorToolHost } from '$lib/editors/railTools';
import type { Selection } from '$lib/editors/overlays';
import type { PixelSize } from '$lib/editors/types';
import type { EffectTool } from '$lib/effects/catalog';
import type { MediaToolInputRef } from '$lib/media-tools/types';
import { beginEdit, discardPending, liveEdit, updatePending, type EditStackState, type PendingEdit, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import type { EffectTile } from './effectTiles';
import { isEffectOp, type ClipInfo, type VideoParams } from './videoSteps';

export interface VideoView {
	readonly ready: boolean;
	readonly url: string | null;
	readonly playing: boolean;
	readonly clip: ClipInfo;
	readonly fps: number;
	readonly base: { start: number; end: number };
	readonly playhead: number;
	readonly outputLength: number;
	readonly displaySize: PixelSize;
	readonly aspect: string;
	readonly sizeOptions: { label: string; short: number }[];
	readonly busy: string | null;
	readonly previewState: 'idle' | 'rendering' | 'ready';
	readonly showClip: boolean;
	readonly tiles: readonly EffectTile[];
	readonly pickedTiles: ReadonlySet<string>;
	readonly effects: ReadonlyMap<string, EffectTool>;
	panelParams(op: string): VideoParams;
	setParams(op: string, params: VideoParams): void;
	setAspect(key: string): void;
	setIn(seconds: number): void;
	setOut(seconds: number): void;
	previewClip(): void;
	cancelPreview(): void;
	frameAction(action: 'save' | 'open'): void;
	pickAudio(from: 'library' | 'upload'): void;
	pickTile(id: string | null): void;
	hoverTile(id: string | null): void;
}

export interface VideoToolHostDeps {
	getStack(): EditStackState<VideoParams>;
	setStack(next: EditStackState<VideoParams>): void;
	fold(state: EditStackState<VideoParams>): EditStackState<VideoParams>;
	applyPending(): void;
	writeEffect(op: string, params: VideoParams): void;
	getSource(): { size: PixelSize | null; ref: MediaToolInputRef | null };
	render(): Promise<HTMLCanvasElement>;
	setApply(state: { label: string; enabled: boolean } | null): void;
	view: VideoView;
}

export interface VideoToolHost extends EditorToolHost<VideoParams> {
	stack: Readable<EditStackState<VideoParams>>;
	video: VideoView;
	syncPending(pending: PendingEdit<VideoParams> | null): void;
	syncStack(stack: EditStackState<VideoParams>): void;
}

export function createVideoToolHost(deps: VideoToolHostDeps): VideoToolHost {
	const pending = writable<PendingEdit<VideoParams> | null>(null);
	const stack = writable<EditStackState<VideoParams>>(deps.getStack());
	const selection: Readable<Selection | null> = readable(null);
	return {
		editor: 'video',
		get source() {
			return deps.getSource();
		},
		video: deps.view,
		pending: { subscribe: pending.subscribe },
		selection,
		stack: { subscribe: stack.subscribe },
		syncPending: (next) => pending.set(next),
		syncStack: (next) => stack.set(next),
		begin: (draft) => (isEffectOp(draft.tool) ? deps.writeEffect(draft.tool, draft.params) : deps.setStack(beginEdit(deps.fold(deps.getStack()), draft))),
		update: (patch: Partial<StepDraft<VideoParams>>) => {
			const held = deps.getStack().pending;
			if (held && patch.params && isEffectOp(held.tool)) deps.writeEffect(held.tool, patch.params);
			else deps.setStack(updatePending(deps.getStack(), patch));
		},
		apply: () => deps.applyPending(),
		discard: () => deps.setStack(discardPending(deps.getStack())),
		live: (draft, match) => deps.setStack(liveEdit(deps.getStack(), draft, match)),
		setApply: (state) => deps.setApply(state),
		render: () => deps.render()
	};
}
