import { effectTools } from '$lib/effects/catalog';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createVideoToolHost, type VideoToolHost, type VideoView } from '$lib/tool-tabs/video/toolHost';
import { defaultParams, type VideoParams } from '$lib/tool-tabs/video/videoSteps';
import type { ToolDTO } from '$lib/media-tools/types';

export interface FakeVideo {
	host: VideoToolHost;
	calls: Array<[string, unknown]>;
	view: VideoView;
	params: Record<string, VideoParams>;
	stack: EditStackState<VideoParams>;
}

export function fakeVideoHost(catalog: readonly ToolDTO[], overrides: Partial<Record<keyof VideoView, unknown>> = {}): FakeVideo {
	const effects = new Map(effectTools(catalog).map((entry) => [entry.id, entry]));
	const clip = { width: 1920, height: 1080, duration: 8 };
	const fake: FakeVideo = { calls: [], params: {}, stack: createStack<VideoParams>(), host: null as never, view: null as never };
	const record = (name: string) => (...args: unknown[]) => void fake.calls.push([name, args.length > 1 ? args : args[0]]);
	const view = {
		ready: true,
		url: '/uploads/dock.mp4',
		playing: false,
		clip,
		fps: 25,
		base: { start: 0, end: clip.duration },
		playhead: 1,
		outputLength: 8,
		displaySize: { width: 1920, height: 1080 },
		aspect: 'free',
		sizeOptions: [{ label: '720p', short: 720 }],
		busy: null,
		previewState: 'idle',
		showClip: false,
		tiles: [],
		pickedTiles: new Set<string>(),
		effects,
		panelParams: (op: string) => fake.params[op] ?? defaultParams(op, clip, 0, effects),
		setParams: (op: string, params: VideoParams) => {
			fake.calls.push(['params', [op, params]]);
			fake.params[op] = params;
		},
		setAspect: record('aspect'),
		setIn: record('in'),
		setOut: record('out'),
		previewClip: record('preview'),
		cancelPreview: record('cancel'),
		frameAction: record('frame'),
		pickAudio: record('audio'),
		pickTile: record('tile'),
		hoverTile: record('hover'),
		...overrides
	} as unknown as VideoView;
	fake.view = view;
	fake.host = createVideoToolHost({
		getStack: () => fake.stack,
		setStack: (next) => {
			fake.stack = next;
			fake.host.syncPending(next.pending);
			fake.host.syncStack(next);
		},
		fold: (state) => state,
		applyPending: () => fake.calls.push(['apply', null]),
		writeEffect: (op, params) => view.setParams(op, params),
		getSource: () => ({ size: { width: clip.width, height: clip.height }, ref: null }),
		render: async () => document.createElement('canvas'),
		setApply: () => undefined,
		view
	});
	return fake;
}
