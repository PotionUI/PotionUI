import { expect, test } from 'vitest';
import { directorIssues, directorProblem } from './director';
import { indexPresets } from './model';
import type { ElixirDefinition } from './types';

const H3 = 'h3';
const presets = indexPresets([
	{
		preset_id: H3,
		preset_name: 'MiniMax-H3',
		mode: 'video',
		mode_label: 'Video',
		inputs: {
			first_frame: { label: 'First frame', media_type: 'image', target: 'video_director_role', role: 'first' },
			last_frame: { label: 'Last frame', media_type: 'image', target: 'video_director_role', role: 'last' }
		},
		outputs: { videos: { label: 'Videos', media_type: 'video' } }
	}
]);

function doc(mode: string, shots = 1) {
	return { mode, segments: Array.from({ length: shots }, (_, index) => ({ id: `s${index}` })), media: [] };
}

function elixir(handle = 'first_frame'): ElixirDefinition {
	return {
		nodes: [
			{ id: 'photos', kind: 'media', media_type: 'image', assets: [] },
			{ id: 'orbit', kind: 'preset', title: 'Orbit left', preset_id: H3, mode: 'video', session_snapshot: { formData: {} } }
		],
		edges: [{ id: 'e1', source: 'photos', source_handle: 'images', target: 'orbit', target_handle: handle }]
	};
}

test('a single shot can take a first frame whatever it starts from', () => {
	for (const mode of ['t2v', 'i2v', 'flf']) expect(directorProblem(doc(mode), 'first', 'X')).toBe(null);
	expect(directorProblem(doc('i2v'), 'last', 'X')).toBe(null);
});

test('what cannot take a frame is explained without mode codes', () => {
	const messages = [
		directorProblem(doc('director', 3), 'first', "Sharpen's session"),
		directorProblem(doc('director'), 'first', "Sharpen's session"),
		directorProblem(doc('t2v'), 'last', "Sharpen's session"),
		directorProblem(null, 'first', "Sharpen's session")
	];

	expect(messages[0]).toBe("Sharpen's session has 3 shots. Elixirs can feed an image into a single-shot session only.");
	for (const message of messages) {
		expect(message).toBeTruthy();
		expect(message).not.toMatch(/\b(t2v|i2v|flf)\b/);
	}
});

test('a multi-shot session behind a frame input is flagged while editing', async () => {
	const prepared: string[] = [];
	const issues = await directorIssues(elixir(), presets, async (node) => {
		prepared.push(node.id);
		return { request: { form_data: { video_director: doc('director', 2) } } };
	});

	expect(prepared).toStrictEqual(['orbit']);
	expect(issues).toStrictEqual([
		{
			code: 'director_unsupported',
			message: "Orbit left's session has 2 shots. Elixirs can feed an image into a single-shot session only.",
			severity: 'error',
			node_id: 'orbit'
		}
	]);
});

test('a session that can take the frame, or cannot be prepared yet, raises nothing', async () => {
	expect(await directorIssues(elixir(), presets, async () => ({ request: { form_data: { video_director: doc('t2v') } } }))).toStrictEqual([]);
	expect(await directorIssues(elixir(), presets, async () => ({ error: 'not ready' }))).toStrictEqual([]);
	expect(await directorIssues({ ...elixir(), edges: [] }, presets, async () => {
			throw new Error('should not prepare');
		})).toStrictEqual([]);
});
