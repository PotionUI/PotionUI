// @vitest-environment jsdom
//
// hydrateExternal fires an async hydrateSegments() call whenever the
// `segments`/`negativeSegments` prop changes and no hydration is already in
// flight -- StageShot et al. hand down a fresh array from `doc` on every
// keystroke. The shared `isHydrating` flag already stops two hydrations from
// running concurrently, but it also makes the reactive trigger SKIP
// re-arming while one is pending -- so a #-chip segment's content can still
// move on (via further external prop updates) while an OLDER hydration for
// the PREVIOUS content is in flight. Without a staleness check, that older
// hydration resolving later commits its now-stale result and silently
// reverts whatever was typed in the meantime. This drives that scenario with
// a controllable deferred promise instead of relying on real async timing,
// so it's deterministic in CI.
import { describe, it, expect, vi, afterEach } from 'vitest';

interface Deferred<T> {
	promise: Promise<T>;
	resolve: (value: T) => void;
}
function deferred<T>(): Deferred<T> {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

interface PendingHydration {
	key: string;
	deferred: Deferred<unknown[]>;
}
let pendingHydrations: PendingHydration[] = [];

function segmentsKey(segments: Array<{ content: string }>): string {
	return segments.map((s) => s.content).join('|');
}

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: (segments: Array<{ content: string }>) => {
		const d = deferred<unknown[]>();
		pendingHydrations.push({ key: segmentsKey(segments), deferred: d });
		return d.promise;
	}
}));

const { default: SegmentedPromptEditor } = await import(
	'../../src/lib/components/SegmentedPromptEditor.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function fakeChip(id: string) {
	return {
		id,
		categoryPath: 'test',
		valueId: id,
		label: id,
		value: id,
		allValues: [{ id, label: id, value: id }],
		shuffle: false,
		autoRegen: false
	};
}

function segment(id: string, content: string, chips: Record<string, unknown> = {}) {
	return { id, content, type: 'content', chips, enabled: true };
}

function resolveHydration(key: string, resultSegments: unknown[]) {
	const idx = pendingHydrations.findIndex((p) => p.key === key);
	if (idx === -1) {
		throw new Error(`no pending hydration for "${key}" -- pending: ${pendingHydrations.map((p) => p.key).join(', ') || '(none)'}`);
	}
	const [entry] = pendingHydrations.splice(idx, 1);
	entry.deferred.resolve(resultSegments);
}

async function flush() {
	for (let i = 0; i < 5; i++) await Promise.resolve();
}

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	pendingHydrations = [];
	vi.restoreAllMocks();
});

describe('SegmentedPromptEditor hydrateExternal: a stale in-flight hydration must not clobber newer text', () => {
	it('discards a hydration whose content fingerprint moved on while it was pending, then re-arms for the current content', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);

		const component = createClassComponent({
			component: SegmentedPromptEditor as never,
			target,
			props: { segments: [segment('a', '', {})] }
		});
		cleanup = () => component.$destroy();

		const emitted: Array<Array<{ content: string; chips: Record<string, unknown> }>> = [];
		component.$on('segmentsChange', (e: CustomEvent<Array<{ content: string; chips: Record<string, unknown> }>>) => {
			emitted.push(e.detail);
		});

		await flush();
		// Initial mount's own hydration (content: '') -- resolve it out of the way.
		resolveHydration('', [segment('a', '', {})]);
		await flush();

		// "type '#alpha'" -- hydration starts for '#alpha', still pending.
		component.$set({ segments: [segment('a', '#alpha', {})] });
		await flush();
		expect(pendingHydrations.map((p) => p.key)).toEqual(['#alpha']);

		// "type ' #beta'" WHILE the '#alpha' hydration is still in flight --
		// isHydrating gates re-arming, so no second hydrateSegments() call
		// starts, but the prop (and its content fingerprint) has moved on.
		component.$set({ segments: [segment('a', '#alpha #beta', {})] });
		await flush();
		expect(pendingHydrations.map((p) => p.key)).toEqual(['#alpha']); // still only the stale one

		// The stale hydration now resolves, based on the OLD '#alpha' snapshot.
		resolveHydration('#alpha', [segment('a', '#alpha', { ch1: fakeChip('ch1') })]);
		await flush();

		// It must never have been committed -- the live text is '#alpha #beta',
		// never reverted to '#alpha'.
		expect(emitted.some((e) => e[0].content === '#alpha')).toBe(false);

		// Discarding (not committing) lets the reactive trigger re-arm for the
		// content that's actually current now that isHydrating dropped.
		expect(pendingHydrations.map((p) => p.key)).toEqual(['#alpha #beta']);
		resolveHydration('#alpha #beta', [segment('a', '#alpha #beta', { ch1: fakeChip('ch1'), ch2: fakeChip('ch2') })]);
		await flush();

		const lastEmitted = emitted[emitted.length - 1];
		expect(lastEmitted[0].content).toBe('#alpha #beta');
		expect(Object.keys(lastEmitted[0].chips)).toHaveLength(2);
	});
});
