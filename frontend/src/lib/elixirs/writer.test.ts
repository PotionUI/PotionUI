import { expect, test } from 'vitest';
import {
	addNode,
	checkConnection,
	connect,
	indexPresets,
	kindNoun,
	mergeSaved,
	nodeLabel,
	nodePorts,
	pinText,
	promptConnected,
	unpinText,
	writerTarget,
	writerTargets
} from './model';
import type { ElixirDefinition } from './types';
import { nodeRunView, nodeStatusLabel, stepState } from './runs';
import { tileKey } from './tiles';
import { tileChoice, nodeChoice } from './choice';

const presets = indexPresets([
	{
		preset_id: 'krea',
		preset_name: 'Krea-2',
		mode: 'enhance',
		mode_label: 'Enhance',
		prompt: true,
		inputs: { source_image: { label: 'Source image', media_type: 'image' } },
		outputs: { images: { label: 'Final images', media_type: 'image' } }
	},
	{
		preset_id: 'seedvr',
		preset_name: 'SeedVR2',
		mode: 'upscale',
		mode_label: 'Upscale',
		prompt: false,
		inputs: { input_image: { label: 'Input image', media_type: 'image' } },
		outputs: { images: { label: 'Upscaled', media_type: 'image' } }
	}
]);

function canvas(): ElixirDefinition {
	return {
		schema_version: 1,
		nodes: [
			{ id: 'photos', kind: 'media', title: 'Photos', media_type: 'image', assets: [] },
			{ id: 'writer', kind: 'llm', title: 'Describe', instruction: 'Describe it', target_node_id: null, pinned: {} },
			{ id: 'restyle', kind: 'preset', title: 'Restyle', preset_id: 'krea', mode: 'enhance' },
			{ id: 'upscale', kind: 'preset', title: null, preset_id: 'seedvr', mode: 'upscale' }
		],
		edges: [],
		viewport: null
	};
}

test('a new prompt writer starts empty with its own label', () => {
	const { definition, id } = addNode(canvas(), 'llm');
	const node = definition.nodes.find((item) => item.id === id)!;
	expect({ kind: node.kind, instruction: node.instruction, target: node.target_node_id, pinned: node.pinned }).toStrictEqual({ kind: 'llm', instruction: '', target: null, pinned: {} });
	expect(nodeLabel(node, presets)).toBe('Untitled prompt writer');
	expect(kindNoun('llm')).toBe('prompt writer');
});

test('a writer takes images or videos and gives text; a prompt preset gains text inputs', () => {
	const definition = canvas();
	expect(Object.keys(nodePorts(definition.nodes[1], presets).inputs)).toStrictEqual(['images', 'videos']);
	expect(nodePorts(definition.nodes[1], presets).outputs.text.media_type).toBe('text');
	const restyle = nodePorts(definition.nodes[2], presets).inputs;
	expect(Object.keys(restyle)).toStrictEqual(['source_image', 'prompt', 'negative_prompt']);
	expect(restyle.prompt.media_type).toBe('text');
	expect(Object.keys(nodePorts(definition.nodes[3], presets).inputs)).toStrictEqual(['input_image']);
});

test('text only goes into a prompt input, and media never does', () => {
	const definition = canvas();
	const toPrompt = { source: 'writer', sourceHandle: 'text', target: 'restyle', targetHandle: 'prompt' };
	const toImage = { source: 'writer', sourceHandle: 'text', target: 'restyle', targetHandle: 'source_image' };
	const mediaToPrompt = { source: 'photos', sourceHandle: 'images', target: 'restyle', targetHandle: 'prompt' };
	expect(checkConnection(definition, toPrompt, presets).ok).toBe(true);
	expect(checkConnection(definition, toImage, presets).reason).toBe('Source image takes images, not prompts.');
	expect(checkConnection(definition, mediaToPrompt, presets).reason).toBe('Prompt takes prompts, not images.');
});

test('a preset keeps its files when a prompt is connected, and one prompt replaces another', () => {
	let definition = connect(canvas(), { source: 'photos', sourceHandle: 'images', target: 'restyle', targetHandle: 'source_image' }, presets).definition;
	definition = connect(definition, { source: 'photos', sourceHandle: 'images', target: 'writer', targetHandle: 'images' }, presets).definition;
	definition = connect(definition, { source: 'writer', sourceHandle: 'text', target: 'restyle', targetHandle: 'prompt' }, presets).definition;
	definition = connect(definition, { source: 'writer', sourceHandle: 'text', target: 'restyle', targetHandle: 'negative_prompt' }, presets).definition;
	expect(definition.edges.filter((edge) => edge.target === 'restyle').map((edge) => edge.target_handle).sort()).toStrictEqual(['negative_prompt', 'prompt', 'source_image']);
	expect(promptConnected(definition, 'restyle')).toBe(true);
	const swapped = connect({ ...definition, nodes: [...definition.nodes, { id: 'second', kind: 'llm', title: 'Second', instruction: 'x', pinned: {} }] }, { source: 'second', sourceHandle: 'text', target: 'restyle', targetHandle: 'prompt' }, presets).definition;
	const prompts = swapped.edges.filter((edge) => edge.target === 'restyle' && edge.target_handle === 'prompt');
	expect(prompts.map((edge) => edge.source)).toStrictEqual(['second']);
	expect(swapped.edges.filter((edge) => edge.target === 'restyle').length).toBe(3);
});

test('a writer writes for its explicit preset or else the preset its text goes to', () => {
	let definition = connect(canvas(), { source: 'writer', sourceHandle: 'text', target: 'restyle', targetHandle: 'prompt' }, presets).definition;
	expect(writerTarget(definition, definition.nodes[1])).toBe('restyle');
	const explicit = { ...definition.nodes[1], target_node_id: 'upscale' };
	expect(writerTarget(definition, explicit)).toBe('upscale');
	expect(writerTarget(definition, { ...explicit, target_node_id: 'gone' })).toBe(null);
	expect(writerTargets(definition, presets)).toStrictEqual([
		{ id: 'restyle', label: 'Restyle', prompt: true },
		{ id: 'upscale', label: 'SeedVR2 · Upscale', prompt: false }
	]);
});

test('pinning keeps a text per file and unpinning drops one or all', () => {
	let definition = pinText(canvas(), 'writer', 'library:p1', 'a fox');
	definition = pinText(definition, 'writer', '-', 'any');
	expect(definition.nodes[1].pinned).toStrictEqual({ 'library:p1': 'a fox', '-': 'any' });
	expect(unpinText(definition, 'writer', 'library:p1').nodes[1].pinned).toStrictEqual({ '-': 'any' });
	expect(unpinText(definition, 'writer').nodes[1].pinned).toStrictEqual({});
	expect(pinText(definition, 'photos', 'k', 'x')).toBe(definition);
});

test('saving never adds media fields to a writer', () => {
	const local = canvas();
	const merged = mergeSaved(local, local, { ...local, nodes: local.nodes.map((node) => ({ ...node, assets: [] })) });
	expect(merged.nodes[1]).toBe(local.nodes[1]);
	expect('assets' in merged.nodes[1]).toBe(false);
});

test('written prompts are keyed, chosen and labelled like files', () => {
	const entry = { kind: 'text', text_id: 'abc', text: 'a fox', for_key: 'library:p1', media_type: 'text' };
	expect(tileKey(entry)).toBe('text:abc');
	const run = {
		status: 'awaiting_selection',
		graph_edges: [{ id: 'e2', source: 'writer', target: 'restyle' }],
		checkpoints: { e2: { eligible: [entry], draft: ['text:abc'], confirmed: null, revision: 1 } },
		node_states: { writer: { status: 'done', manifest: { text: [entry] } } },
		invocations: [{ node_id: 'writer', input_key: 'library:p1', status: 'completed', attempt: 1, ordinal: 0, outputs: [entry] }]
	};
	const choice = nodeChoice(run, 'writer', { labelFor: () => 'Restyle' });
	expect(tileChoice(choice, 'text:abc').tick).toStrictEqual({ checked: true, locked: false, label: 'Restyle' });
	const view = nodeRunView(run, { id: 'writer', kind: 'llm' })!;
	expect(view.writer).toBe(true);
	expect(view.outputs[0].text).toBe('a fox');
});

test('a writer reads as writing, not generating', () => {
	expect(nodeStatusLabel('running', { done: 1, total: 3, writer: true })).toBe('Writing · 1 of 3');
	expect(nodeStatusLabel('queued', { total: 2, writer: true })).toBe('Queued · 2 prompts');
	expect(nodeStatusLabel('done', { outputs: 1, writer: true })).toBe('Wrote 1 prompt');
	expect(nodeStatusLabel('running', { done: 0, total: 2 })).toBe('Generating · 0 of 2');
	expect(stepState({ status: 'running', writer: true }).word).toBe('Writing');
});
