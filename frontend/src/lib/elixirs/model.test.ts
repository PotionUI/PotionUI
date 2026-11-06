import { expect, test } from 'vitest';
import {
	MAX_PROMPTS_PER_INPUT,
	pinKey,
	promptHeading,
	promptsPerInput,
	addNode,
	checkConnection,
	choosePreset,
	connect,
	emptyDefinition,
	formOverrides,
	indexPresets,
	issuesByTarget,
	mediaTypeOf,
	mergePicked,
	mergeSaved,
	nodeLabel,
	nodePorts,
	pickerKeys,
	removeElements,
	summarizeSnapshot,
	worstSeverity
} from './model';
import type { ElixirDefinition, ElixirNode, MediaAsset } from './types';

type NodeSet = Pick<ElixirDefinition, 'nodes'>;

const H3 = '01KX47H3MINIMAX000000000VA';
const KREA = '4TK1KBQZ2XMB8ME0PTMXS1YJQP';

const presets = indexPresets([
	{
		preset_id: KREA,
		preset_name: 'Krea-2',
		mode: 'enhance',
		mode_label: 'Enhance',
		inputs: { source_image: { label: 'Source image', media_type: 'image' } },
		outputs: { images: { label: 'Enhanced images', media_type: 'image' } }
	},
	{
		preset_id: H3,
		preset_name: 'MiniMax-H3',
		mode: 'video',
		mode_label: 'Video',
		inputs: {
			first_frame: { label: 'First frame', media_type: 'image' },
			last_frame: { label: 'Last frame', media_type: 'image' }
		},
		outputs: { videos: { label: 'Final videos', media_type: 'video' } }
	}
]);

function graph(): ElixirDefinition {
	return {
		...emptyDefinition(),
		nodes: [
			{ id: 'photos', kind: 'media', media_type: 'image', assets: [] },
			{ id: 'clips', kind: 'media', media_type: 'video', assets: [] },
			{ id: 'orbit', kind: 'preset', preset_id: H3, mode: 'video', title: 'Orbit left' },
			{ id: 'sharpen', kind: 'preset', preset_id: KREA, mode: 'enhance' },
			{ id: 'polish', kind: 'preset', preset_id: KREA, mode: 'enhance' }
		],
		edges: []
	};
}

function link(source: string, sourceHandle: string, target: string, targetHandle: string) {
	return { source, sourceHandle, target, targetHandle };
}

test('ports come from the contract or the media type', () => {
	const nodes = graph().nodes;
	expect(Object.keys(nodePorts(nodes[0], presets).outputs)).toStrictEqual(['images']);
	expect(Object.keys(nodePorts(nodes[1], presets).outputs)).toStrictEqual(['videos']);
	expect(Object.keys(nodePorts(nodes[2], presets).inputs)).toStrictEqual(['first_frame', 'last_frame']);
	expect(nodePorts({ kind: 'media', media_type: null }, presets).outputs).toStrictEqual({});
	expect(nodePorts({ kind: 'preset', preset_id: null }, presets)).toStrictEqual({ inputs: {}, outputs: {} });
});

test('nodes are named by title, then by preset and mode', () => {
	const nodes = graph().nodes;
	expect(nodeLabel(nodes[2], presets)).toBe('Orbit left');
	expect(nodeLabel(nodes[3], presets)).toBe('Krea-2 · Enhance');
	expect(nodeLabel(nodes[0], presets)).toBe('Untitled media source');
	expect(nodeLabel({ kind: 'preset', title: '  ' }, presets)).toBe('Untitled preset node');
});

test('a compatible connection is added and a second one into the same node replaces it', () => {
	const first = connect(graph(), link('photos', 'images', 'orbit', 'first_frame'), presets);
	expect(first.verdict.ok).toBe(true);
	expect(first.definition.edges.length).toBe(1);

	const second = connect(first.definition, link('sharpen', 'images', 'orbit', 'last_frame'), presets);
	expect(second.definition.edges.length).toBe(1);
	expect(second.definition.edges[0].source).toBe('sharpen');
	expect(second.definition.edges[0].target_handle).toBe('last_frame');
});

test('one source can feed several nodes', () => {
	let definition = connect(graph(), link('photos', 'images', 'orbit', 'first_frame'), presets).definition;
	definition = connect(definition, link('photos', 'images', 'sharpen', 'source_image'), presets).definition;
	expect(definition.edges.length).toBe(2);
});

test('wrong media, media targets, self links and loops are refused', () => {
	const base = graph();
	expect(checkConnection(base, link('clips', 'videos', 'orbit', 'first_frame'), presets).reason).toMatch(/takes images/);
	expect(checkConnection(base, link('photos', 'images', 'clips', 'videos'), presets).reason).toMatch(/media source/);
	expect(checkConnection(base, link('sharpen', 'images', 'sharpen', 'source_image'), presets).reason).toMatch(/itself/);
	const linked = connect(base, link('sharpen', 'images', 'polish', 'source_image'), presets).definition;
	const loop = checkConnection(linked, link('polish', 'images', 'sharpen', 'source_image'), presets);
	expect(loop.ok).toBe(false);
	expect(loop.reason).toMatch(/loop/);
	expect(connect(linked, link('polish', 'images', 'sharpen', 'source_image'), presets).definition).toBe(linked);
});

test('replacing the only incoming connection cannot be mistaken for a loop', () => {
	const linked = connect(graph(), link('sharpen', 'images', 'polish', 'source_image'), presets).definition;
	expect(checkConnection(linked, link('photos', 'images', 'polish', 'source_image'), presets).ok).toBe(true);
});

test('removing a node removes its connections', () => {
	let definition = connect(graph(), link('photos', 'images', 'orbit', 'first_frame'), presets).definition;
	definition = connect(definition, link('photos', 'images', 'sharpen', 'source_image'), presets).definition;
	const removed = removeElements(definition, ['orbit']);
	expect(removed.nodes.some((node) => node.id === 'orbit')).toBe(false);
	expect(removed.edges.map((edge) => edge.target)).toStrictEqual(['sharpen']);
	expect(removeElements(definition, [], [definition.edges[0].id]).edges.length).toBe(1);
});

test('new nodes get unique ids and start empty', () => {
	const first = addNode(emptyDefinition(), 'preset');
	const second = addNode(first.definition, 'media');
	expect(first.id).not.toBe(second.id);
	expect(first.id).toMatch(/^n[a-z0-9]+$/);
	const media = second.definition.nodes[1];
	expect([media.kind, media.media_type, media.assets]).toStrictEqual(['media', null, []]);
	expect('position' in media).toBe(false);
});

test('changing the preset drops the copied session and local edits', () => {
	const definition: ElixirDefinition = {
		...emptyDefinition(),
		nodes: [{ id: 'n', kind: 'preset', preset_id: KREA, mode: 'enhance', overrides: { prompt: 'x' }, session_snapshot: {}, session_source: {} }]
	};
	const same = choosePreset(definition, 'n', KREA, 'enhance');
	const changed = choosePreset(definition, 'n', H3, 'video').nodes[0];
	expect(same).toBe(definition);
	expect([changed.session_snapshot, changed.session_source, changed.overrides]).toStrictEqual([null, null, {}]);
});

test('picker keys and picked items round-trip per kind', () => {
	const assets: MediaAsset[] = [
		{ kind: 'library', item_id: 'lib1', media_type: 'image', preview_url: '/a' },
		{ kind: 'history', generation_id: 'g', file_id: 'f1', media_type: 'image' }
	];
	expect(pickerKeys(assets, 'history')).toStrictEqual(['g:f1']);
	expect(pickerKeys(assets, 'library')).toStrictEqual(['lib1']);

	const merged = mergePicked(assets, 'history', [
		{ key: 'g:f1', origin: { kind: 'history', generationId: 'g', fileId: 'f1' } },
		{ key: 'g:f2', mediaType: 'image', filename: 'b.png', origin: { kind: 'history', generationId: 'g', fileId: 'f2' } }
	]);
	expect(merged.map((asset) => asset.item_id ?? asset.file_id)).toStrictEqual(['lib1', 'f1', 'f2']);
	expect(merged[0].preview_url).toBe('/a');
	expect(merged[2].original_filename).toBe('b.png');

	const cleared = mergePicked(merged, 'history', []);
	expect(cleared.map((asset) => asset.kind)).toStrictEqual(['library']);
});

test('a media type is only inferred from a single-type selection', () => {
	expect(mediaTypeOf([{ media_type: 'video' }, { media_type: 'video' }])).toBe('video');
	expect(mediaTypeOf([{ media_type: 'video' }, { media_type: 'image' }])).toBe(null);
	expect(mediaTypeOf([])).toBe(null);
});

test('saved server fields are taken only where the user has not changed the node since', () => {
	const sent: NodeSet = {
		nodes: [
			{ id: 'm', kind: 'media', media_type: null, assets: [{ kind: 'library', item_id: 'a' }] },
			{ id: 'p', kind: 'preset', preset_id: KREA, mode: 'enhance', form_name: null },
			{ id: 'q', kind: 'preset', preset_id: KREA, mode: 'enhance', form_name: null }
		]
	};
	const saved: NodeSet = {
		nodes: [
			{ id: 'm', kind: 'media', media_type: 'image', source_revision: 2, assets: [{ kind: 'library', item_id: 'a', preview_url: '/p' }] },
			{ id: 'p', kind: 'preset', preset_id: KREA, mode: 'enhance', session_snapshot: { prompt: 's' }, form_name: 'custom' },
			{ id: 'q', kind: 'preset', preset_id: KREA, mode: 'enhance', session_snapshot: { prompt: 's' } }
		]
	};
	const local: NodeSet = {
		nodes: [
			{ ...sent.nodes[0], position: { x: 9, y: 9 } },
			sent.nodes[1],
			{ ...sent.nodes[2], preset_id: H3, mode: 'video' }
		]
	};
	const merged = mergeSaved(local, sent, saved);
	expect(merged.nodes[0].assets![0].preview_url).toBe('/p');
	expect(merged.nodes[0].media_type).toBe('image');
	expect(merged.nodes[0].position).toStrictEqual({ x: 9, y: 9 });
	expect(merged.nodes[1].session_snapshot!.prompt).toBe('s');
	expect(merged.nodes[1].form_name).toBe('custom');
	expect(merged.nodes[2].session_snapshot).toBe(undefined);

	const changedMedia = mergeSaved({ nodes: [{ ...sent.nodes[0], assets: [] }] }, sent, saved);
	expect(changedMedia.nodes[0].assets).toStrictEqual([]);
});

test('issues are grouped by node and connection', () => {
	const grouped = issuesByTarget({
		issues: [
			{ code: 'a', severity: 'error', node_id: 'n1' },
			{ code: 'b', severity: 'warning', node_id: 'n1', edge_id: 'e1' },
			{ code: 'c', severity: 'error' }
		]
	});
	expect(grouped.nodes.get('n1')!.length).toBe(2);
	expect(grouped.edges.get('e1')![0].code).toBe('b');
	expect(grouped.general[0].code).toBe('c');
	expect(worstSeverity(grouped.nodes.get('n1'))).toBe('error');
	expect(worstSeverity(grouped.edges.get('e1'))).toBe('warning');
	expect(worstSeverity([])).toBe(null);
	expect(worstSeverity([{ code: 'no_media', severity: 'error' }])).toBe('setup');
	expect(worstSeverity([{ code: 'no_media', severity: 'error' }, { code: 'cycle', severity: 'error' }])).toBe('error');
});

test('the settings summary reads prompts, seed, settings and local edits', () => {
	const summary = summarizeSnapshot(
		{
			prompt: 'old',
			promptSegments: [{ content: 'slow orbit' }, { content: 'left' }],
			negativePrompt: 'blur',
			selectedVariant: 'custom',
			videoDirector: { mode: 'i2v' },
			formData: { quantity: 2, steps: 24, seed: 7, keyframes: [{ path: 'x' }], empty: '' }
		},
		{ formData: { steps: 4 } }
	)!;
	expect(summary.prompt).toBe('slow orbit left');
	expect(summary.negative).toBe('blur');
	expect(summary.seed).toBe(7);
	expect(summary.director!.mode).toBe('i2v');
	expect(summary.settings.map((item) => `${item.name}=${item.value}`)).toStrictEqual(['quantity=2', 'seed=7', 'steps=4']);
	expect(summary.media).toStrictEqual(['keyframes']);
	expect(summary.edited).toBe(true);
	expect(summarizeSnapshot(null)).toBe(null);
});

test('form edits become overrides relative to the copied session', () => {
	const saved = { steps: 8, quantity: 1, seed: 7 };
	const baseline = { steps: 8, quantity: 1, seed: 7, sampler: 'euler' };

	const edited = formOverrides({}, saved, baseline, { ...baseline, steps: 24 });
	expect(edited).toStrictEqual({ formData: { steps: 24 } });

	const again = formOverrides(edited, saved, { ...baseline, steps: 24 }, { ...baseline, steps: 24, quantity: 3 });
	expect(again).toStrictEqual({ formData: { steps: 24, quantity: 3 } });

	const reverted = formOverrides(again, saved, { ...baseline, steps: 24, quantity: 3 }, { ...baseline, quantity: 3 });
	expect(reverted).toStrictEqual({ formData: { quantity: 3 } });

	const clean = formOverrides({ prompt: 'mine', formData: { quantity: 3 } }, saved, { ...baseline, quantity: 3 }, baseline);
	expect(clean).toStrictEqual({ prompt: 'mine' });

	expect(formOverrides({}, saved, baseline, baseline)).toStrictEqual({});
});

test('a save takes the server-owned session fields, so a followed node keeps its session state', () => {
	const ref = { id: 's1', name: 'Bottle orbit', version: 4, deleted: false };
	const node: ElixirNode = { id: 'p', kind: 'preset', preset_id: KREA, mode: 'enhance', session_mode: 'copy', overrides: {} };
	const saved = { nodes: [{ ...node, session_mode: 'follow', session_ref: ref, session_hash: 'h2', session_snapshot: { prompt: 'x' } }] };
	const merged = mergeSaved({ nodes: [node] }, { nodes: [node] }, saved);
	expect(merged.nodes[0].session_mode).toBe('follow');
	expect(merged.nodes[0].session_ref).toStrictEqual(ref);
	expect(merged.nodes[0].session_hash).toBe('h2');
});

test('a written prompt is pinned by its own slot and labelled by its place for its input', () => {
	const second = { for_key: 'library:p1', slot_key: 'library:p1#2', prompt_number: 2, prompt_count: 3 };
	expect(pinKey(second)).toBe('library:p1#2');
	expect(pinKey({ for_key: 'library:p1' })).toBe('library:p1');
	expect(promptHeading(second, { 'library:p1': 'p1.png' }, 4, 6)).toBe('p1.png · prompt 2 of 3');
	expect(promptHeading(second, {}, 4, 6)).toBe('Prompt 2 of 3');
	expect(promptHeading({ for_key: 'x' }, {}, 1, 2)).toBe('Prompt 2');
	expect(promptHeading({ for_key: 'x' }, { x: 'x.png' }, 0, 1)).toBe('x.png');
});

test('prompts per input defaults to one and stays within the cap', () => {
	expect(promptsPerInput({})).toBe(1);
	expect(promptsPerInput({ prompts_per_input: 4 })).toBe(4);
	expect(promptsPerInput({ prompts_per_input: 99 })).toBe(MAX_PROMPTS_PER_INPUT);
	expect(promptsPerInput({ prompts_per_input: 0 })).toBe(1);
});
