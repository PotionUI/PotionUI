import { describe, expect, it } from 'vitest';
import type { SessionData } from '$lib/types/api';
import type { Segment } from '$lib/types/segments';
import { fieldValue, mergeModeData, mergeSessionData, sameValue, withFieldValue } from './sessionMerge';

const seg = (id: string, content: string): Segment => ({ id, content });
const join = (segments: Segment[]) => segments.map((segment) => segment.content).join(', ');
const ids = (segments: Segment[] | undefined) => (segments ?? []).map((segment) => segment.id);

function entry(overrides: Partial<SessionData> = {}): SessionData {
	return {
		selectedVariant: 'default',
		promptSegments: [seg('a', 'a cat'), seg('b', 'on a roof')],
		negativePromptSegments: [],
		formData: { steps: 20, cfg: 4 },
		...overrides
	};
}

function merge(base: SessionData | undefined, local: SessionData, remote: SessionData, baseKnown = true) {
	return mergeModeData('txt2img', base, local, remote, baseKnown, { resolvePrompt: (segments) => join(segments) });
}

const keys = (changes: { key: string }[]) => changes.map((change) => change.key).sort();

describe('sameValue', () => {
	it('compares objects structurally regardless of key order', () => {
		expect(sameValue({ a: 1, b: [1, { c: 2 }] }, { b: [1, { c: 2 }], a: 1 })).toBe(true);
		expect(sameValue({ a: 1 }, { a: 1, b: undefined })).toBe(true);
		expect(sameValue([1, 2], [2, 1])).toBe(false);
	});
});

describe('form fields', () => {
	it('applies a remote change to a field the tab has not touched', () => {
		const base = entry();
		const result = merge(base, entry(), entry({ formData: { steps: 30, cfg: 4 } }));

		expect(result.merged.formData).toEqual({ steps: 30, cfg: 4 });
		expect(keys(result.applied)).toEqual(['form:steps']);
		expect(result.conflicts).toEqual([]);
	});

	it('keeps a local edit when the remote left that field alone', () => {
		const result = merge(entry(), entry({ formData: { steps: 20, cfg: 7 } }), entry({ formData: { steps: 30, cfg: 4 } }));

		expect(result.merged.formData).toEqual({ steps: 30, cfg: 7 });
		expect(result.theirs.formData).toEqual({ steps: 30, cfg: 4 });
		expect(result.conflicts).toEqual([]);
	});

	it('raises a conflict on the one field both sides changed differently', () => {
		const result = merge(
			entry(),
			entry({ formData: { steps: 25, cfg: 4 } }),
			entry({ formData: { steps: 30, cfg: 5 } })
		);

		expect(result.conflicts).toEqual([{ mode: 'txt2img', key: 'form:steps', local: 25, remote: 30 }]);
		expect(result.merged.formData).toEqual({ steps: 25, cfg: 5 });
		expect(result.theirs.formData).toEqual({ steps: 30, cfg: 5 });
	});

	it('is quiet when both sides made the same change', () => {
		const same = entry({ formData: { steps: 30, cfg: 4 } });
		const result = merge(entry(), same, same);

		expect(result.applied).toEqual([]);
		expect(result.conflicts).toEqual([]);
	});

	it('treats a field the remote write left out as untouched', () => {
		const result = merge(entry(), entry({ formData: { steps: 20, cfg: 9 } }), entry({ formData: { steps: 20 } }));

		expect(result.merged.formData).toEqual({ steps: 20, cfg: 9 });
		expect(result.theirs.formData).toEqual({ steps: 20, cfg: 4 });
		expect(result.applied).toEqual([]);
	});
});

describe('variables', () => {
	const mood = { type: 'text', value: 'noir' } as const;
	const sunny = { type: 'text', value: 'sunlit' } as const;

	it('adds, changes and removes variables the tab did not touch', () => {
		const base = entry({ variables: { mood, old: mood } });
		const remote = entry({ variables: { mood: sunny, fresh: mood } });
		const result = merge(base, base, remote);

		expect(result.merged.variables).toEqual({ mood: sunny, fresh: mood });
		expect(keys(result.applied)).toEqual(['var:fresh', 'var:mood', 'var:old']);
	});

	it('conflicts on a variable both sides changed differently', () => {
		const base = entry({ variables: { mood } });
		const result = merge(base, entry({ variables: { mood: { type: 'text', value: 'grim' } } }), entry({ variables: { mood: sunny } }));

		expect(keys(result.conflicts)).toEqual(['var:mood']);
		expect(result.merged.variables).toEqual({ mood: { type: 'text', value: 'grim' } });
	});
});

describe('preset variant', () => {
	it('switches the variant when only the remote changed it', () => {
		const result = merge(entry(), entry(), entry({ selectedVariant: 'turbo' }));

		expect(result.merged.selectedVariant).toBe('turbo');
		expect(keys(result.applied)).toEqual(['selectedVariant']);
	});

	it('conflicts when both sides picked different variants', () => {
		const result = merge(entry(), entry({ selectedVariant: 'hq' }), entry({ selectedVariant: 'turbo' }));

		expect(result.conflicts).toEqual([{ mode: 'txt2img', key: 'selectedVariant', local: 'hq', remote: 'turbo' }]);
		expect(result.merged.selectedVariant).toBe('hq');
	});

	it('keeps the local variant when the remote write omits it', () => {
		const result = merge(entry(), entry({ selectedVariant: 'hq' }), entry({ selectedVariant: undefined }));

		expect(result.merged.selectedVariant).toBe('hq');
		expect(result.conflicts).toEqual([]);
	});
});

describe('prompt segments', () => {
	it('applies remote content to a segment the tab did not edit and rebuilds the prompt', () => {
		const remote = entry({ promptSegments: [seg('a', 'a dog'), seg('b', 'on a roof')], prompt: 'a dog, on a roof' });
		const result = merge(entry(), entry(), remote);

		expect(result.merged.promptSegments).toEqual(remote.promptSegments);
		expect(result.merged.prompt).toBe('a dog, on a roof');
		expect(keys(result.applied)).toEqual(['seg:promptSegments:a']);
	});

	it('conflicts on one segment edited on both sides and merges the rest', () => {
		const local = entry({ promptSegments: [seg('a', 'a lynx'), seg('b', 'on a roof')] });
		const remote = entry({ promptSegments: [seg('a', 'a dog'), seg('b', 'at dusk')] });
		const result = merge(entry(), local, remote);

		expect(result.conflicts).toEqual([
			{ mode: 'txt2img', key: 'seg:promptSegments:a', local: seg('a', 'a lynx'), remote: seg('a', 'a dog') }
		]);
		expect(result.merged.promptSegments).toEqual([seg('a', 'a lynx'), seg('b', 'at dusk')]);
		expect(result.merged.prompt).toBe('a lynx, at dusk');
		expect(result.theirs.promptSegments).toEqual(remote.promptSegments);
	});

	it('inserts a remote segment at its remote position and keeps a local addition', () => {
		const local = entry({ promptSegments: [seg('a', 'a cat'), seg('b', 'on a roof'), seg('l', 'mine')] });
		const remote = entry({ promptSegments: [seg('a', 'a cat'), seg('r', 'theirs'), seg('b', 'on a roof')] });
		const result = merge(entry(), local, remote);

		expect(ids(result.merged.promptSegments)).toEqual(['a', 'r', 'b', 'l']);
		expect(keys(result.applied)).toEqual(['seg:promptSegments:r']);
	});

	it('drops a segment the remote removed when the tab left it alone', () => {
		const result = merge(entry(), entry(), entry({ promptSegments: [seg('b', 'on a roof')] }));

		expect(ids(result.merged.promptSegments)).toEqual(['b']);
	});

	it('keeps a segment the tab deleted when the remote left it alone', () => {
		const result = merge(entry(), entry({ promptSegments: [seg('b', 'on a roof')] }), entry({ formData: { steps: 9, cfg: 4 } }));

		expect(ids(result.merged.promptSegments)).toEqual(['b']);
		expect(result.conflicts).toEqual([]);
	});

	it('conflicts and shows the segment when the tab deleted what the remote edited', () => {
		const result = merge(
			entry(),
			entry({ promptSegments: [seg('b', 'on a roof')] }),
			entry({ promptSegments: [seg('a', 'a dog'), seg('b', 'on a roof')] })
		);

		expect(result.conflicts).toEqual([
			{ mode: 'txt2img', key: 'seg:promptSegments:a', local: undefined, remote: seg('a', 'a dog') }
		]);
		expect(ids(result.merged.promptSegments)).toEqual(['a', 'b']);
	});

	it('conflicts and keeps the segment when the remote deleted what the tab edited', () => {
		const result = merge(
			entry(),
			entry({ promptSegments: [seg('a', 'a lynx'), seg('b', 'on a roof')] }),
			entry({ promptSegments: [seg('b', 'on a roof')] })
		);

		expect(result.conflicts).toEqual([
			{ mode: 'txt2img', key: 'seg:promptSegments:a', local: seg('a', 'a lynx'), remote: undefined }
		]);
		expect(ids(result.merged.promptSegments)).toEqual(['a', 'b']);
	});

	it('takes the remote order when the tab did not reorder', () => {
		const result = merge(entry(), entry(), entry({ promptSegments: [seg('b', 'on a roof'), seg('a', 'a cat')] }));

		expect(ids(result.merged.promptSegments)).toEqual(['b', 'a']);
	});

	it('keeps the local order when the tab reordered, while still taking remote content', () => {
		const local = entry({ promptSegments: [seg('b', 'on a roof'), seg('a', 'a cat')] });
		const remote = entry({ promptSegments: [seg('a', 'a dog'), seg('b', 'on a roof')] });
		const result = merge(entry(), local, remote);

		expect(result.merged.promptSegments).toEqual([seg('b', 'on a roof'), seg('a', 'a dog')]);
		expect(result.conflicts).toEqual([]);
	});

	it('leaves the segments alone when the remote write carries none', () => {
		const local = entry({ promptSegments: [seg('a', 'a lynx'), seg('b', 'on a roof')] });
		const remote: SessionData = { formData: { steps: 20, cfg: 4 }, prompt: 'plain text' };
		const result = merge(entry(), local, remote);

		expect(result.merged.promptSegments).toEqual(local.promptSegments);
		expect(result.theirs.promptSegments).toEqual(entry().promptSegments);
		expect(result.conflicts).toEqual([]);
	});

	it('reads the legacy segments key of an old remote payload', () => {
		const remote = { ...entry(), promptSegments: undefined, segments: [seg('a', 'a dog'), seg('b', 'on a roof')] };
		const result = merge(entry(), entry(), remote);

		expect(result.merged.promptSegments?.[0]).toEqual(seg('a', 'a dog'));
	});
});

describe('plain prompt text', () => {
	it('merges the prompt as one field when no segments exist', () => {
		const plain = (prompt: string): SessionData => ({ prompt, promptSegments: [], negativePromptSegments: [] });
		const applied = merge(plain('a'), plain('a'), plain('b'));
		const clashed = merge(plain('a'), plain('mine'), plain('theirs'));

		expect(applied.merged.prompt).toBe('b');
		expect(keys(clashed.conflicts)).toEqual(['prompt']);
	});
});

describe('without a known base', () => {
	it('turns every differing field into a conflict', () => {
		const result = merge(undefined, entry({ formData: { steps: 20, cfg: 4 } }), entry({ formData: { steps: 30, cfg: 4 } }), false);

		expect(keys(result.conflicts)).toEqual(['form:steps']);
		expect(result.merged.formData).toEqual({ steps: 20, cfg: 4 });
	});
});

describe('mergeSessionData', () => {
	it('merges every mode slot independently and keeps local-only modes', () => {
		const base = { txt2img: entry(), img2img: entry({ formData: { denoise: 0.5 } }) };
		const local = { txt2img: entry({ formData: { steps: 22, cfg: 4 } }), img2img: base.img2img, video: entry() };
		const remote = { txt2img: entry(), img2img: entry({ formData: { denoise: 0.7 } }) };

		const result = mergeSessionData(base, local, remote);

		expect(result.merged.txt2img.formData).toEqual({ steps: 22, cfg: 4 });
		expect(result.merged.img2img.formData).toEqual({ denoise: 0.7 });
		expect(result.merged.video).toEqual(entry());
		expect(result.applied).toEqual([{ mode: 'img2img', key: 'form:denoise' }]);
	});

	it('takes a mode slot the tab never had', () => {
		const result = mergeSessionData({ txt2img: entry() }, { txt2img: entry() }, { txt2img: entry(), video: entry({ seed: 7 }) });

		expect(result.merged.video.seed).toBe(7);
	});
});

describe('fieldValue and withFieldValue', () => {
	it('read and write one field by its key', () => {
		const data = entry({ variables: { mood: { type: 'text', value: 'noir' } } });

		expect(fieldValue(data, 'form:steps')).toBe(20);
		expect(fieldValue(data, 'seg:promptSegments:b')).toEqual(seg('b', 'on a roof'));
		expect(fieldValue(data, 'selectedVariant')).toBe('default');

		const edited = withFieldValue(data, 'seg:promptSegments:a', seg('a', 'a dog'), { resolvePrompt: join });
		expect(edited.promptSegments).toEqual([seg('a', 'a dog'), seg('b', 'on a roof')]);
		expect(edited.prompt).toBe('a dog, on a roof');
		expect(ids(withFieldValue(data, 'seg:promptSegments:a', undefined).promptSegments)).toEqual(['b']);
		expect(withFieldValue(data, 'form:cfg', 6).formData).toEqual({ steps: 20, cfg: 6 });
		expect(withFieldValue(data, 'var:mood', undefined).variables).toBeUndefined();
	});
});
