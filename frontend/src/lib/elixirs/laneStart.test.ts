import { expect, test } from 'vitest';
import { blankNode, indexPresets } from './model';
import { fileEdge, insertStartBefore, laneTree, promptEdge, startNeeds, startPresetChoices } from './lane';
import type { ElixirDefinition, ElixirEdge, ElixirNode, PresetEntry } from './types';

const PRESETS: PresetEntry[] = [
	{ preset_id: 'krea', preset_name: 'Krea-2', mode: 't2i', mode_label: 'Text to image', prompt: true, inputs: {}, outputs: { images: { label: 'Images', media_type: 'image' } } },
	{ preset_id: 'wan', preset_name: 'Wan', mode: 'i2v', mode_label: 'Image to video', prompt: true, inputs: { first_frame: { label: 'First frame', media_type: 'image' } }, outputs: { videos: { label: 'Videos', media_type: 'video' } } },
	{ preset_id: 'seed', preset_name: 'SeedVR2', mode: 'vup', mode_label: 'Video upscale', prompt: false, inputs: { video: { label: 'Video', media_type: 'video' } }, outputs: { videos: { label: 'Videos', media_type: 'video' } } },
	{ preset_id: 'qwen', preset_name: 'Qwen', mode: 'edit', mode_label: 'Image edit', prompt: true, inputs: { reference: { label: 'Reference image', media_type: 'image' } }, outputs: { images: { label: 'Images', media_type: 'image' } } }
];
const presets = indexPresets(PRESETS);

function preset(id: string, presetId: string, mode: string): ElixirNode {
	return { id, kind: 'preset', title: null, preset_id: presetId, mode, overrides: {}, session_source: null, session_snapshot: null };
}

function edge(id: string, source: string, source_handle: string, target: string, target_handle: string): ElixirEdge {
	return { id, source, source_handle, target, target_handle, iteration: 'each' };
}

function chain(): ElixirDefinition {
	return {
		schema_version: 1,
		viewport: null,
		nodes: [preset('orbit', 'wan', 'i2v'), preset('up', 'seed', 'vup')],
		edges: [edge('e1', 'orbit', 'videos', 'up', 'video')]
	};
}

test('a start that takes an image can be fed by a step that makes images or by files', () => {
	const needs = startNeeds(chain(), 'orbit', presets);
	expect(needs).toEqual({ port: 'first_frame', type: 'image', writer: true, files: true });
	const choices = startPresetChoices(PRESETS, needs!, 'preset').map((item) => item.preset_id);
	expect(choices).toEqual(['krea', 'qwen']);
});

test('a start that takes nothing but a prompt can only be fed by a writer', () => {
	const definition: ElixirDefinition = { schema_version: 1, viewport: null, nodes: [preset('photo', 'krea', 't2i')], edges: [] };
	const needs = startNeeds(definition, 'photo', presets);
	expect(needs).toMatchObject({ files: false, writer: true });
	expect(startPresetChoices(PRESETS, needs!, 'preset')).toEqual([]);
});

test('files and later steps cannot take a new start', () => {
	const definition: ElixirDefinition = { schema_version: 1, viewport: null, nodes: [{ ...blankNode('media', 'm'), media_type: 'image' }, preset('a', 'wan', 'i2v')], edges: [edge('e', 'm', 'images', 'a', 'first_frame')] };
	expect(startNeeds(definition, 'm', presets)).toBeNull();
	expect(startNeeds(definition, 'a', presets)).toBeNull();
});

test('a step before the start becomes the start and shifts everything one column right', () => {
	const before = chain();
	const beforeTree = laneTree(before);
	const result = insertStartBefore(before, 'orbit', { kind: 'preset', presetId: 'krea', mode: 't2i' }, presets);
	expect(result.id).toBeTruthy();
	const tree = laneTree(result.definition);
	expect(tree.roots).toEqual([result.id]);
	expect(tree.depth.get('orbit')).toBe((beforeTree.depth.get('orbit') ?? 0) + 1);
	expect(tree.depth.get('up')).toBe((beforeTree.depth.get('up') ?? 0) + 1);
	expect(tree.columns).toBe(beforeTree.columns + 1);
	expect(fileEdge(result.definition, 'orbit')).toMatchObject({ source: result.id, source_handle: 'images', target_handle: 'first_frame' });
});

test('node ids and existing edges stay as they were', () => {
	const before = chain();
	const result = insertStartBefore(before, 'orbit', { kind: 'preset', presetId: 'krea', mode: 't2i' }, presets);
	expect(result.definition.nodes.map((node) => node.id)).toEqual([result.id, 'orbit', 'up']);
	expect(result.definition.edges).toContainEqual(before.edges[0]);
	expect(result.definition.nodes.find((node) => node.id === 'orbit')).toBe(before.nodes[0]);
});

test('a files source is created with the type the start needs', () => {
	const result = insertStartBefore(chain(), 'orbit', { kind: 'files' }, presets);
	const added = result.definition.nodes.find((node) => node.id === result.id);
	expect(added).toMatchObject({ kind: 'media', media_type: 'image' });
	expect(fileEdge(result.definition, 'orbit')?.source).toBe(result.id);
});

test('a writer feeds the prompt of the start', () => {
	const result = insertStartBefore(chain(), 'orbit', { kind: 'writer' }, presets);
	expect(promptEdge(result.definition, 'orbit')).toMatchObject({ source: result.id, source_handle: 'text', target_handle: 'prompt' });
	expect(laneTree(result.definition).roots).toEqual([result.id]);
});

test('a step that cannot feed the start is refused and nothing changes', () => {
	const before = chain();
	expect(insertStartBefore(before, 'orbit', { kind: 'preset', presetId: 'seed', mode: 'vup' }, presets)).toEqual({ definition: before, id: null });
	expect(insertStartBefore(before, 'up', { kind: 'files' }, presets)).toEqual({ definition: before, id: null });
});

test('a prompt writer start can be fed by a step that makes images', () => {
	const definition: ElixirDefinition = { schema_version: 1, viewport: null, nodes: [blankNode('llm', 'w'), preset('p', 'wan', 'i2v')], edges: [edge('e', 'w', 'text', 'p', 'prompt')] };
	const needs = startNeeds(definition, 'w', presets);
	expect(needs).toMatchObject({ files: true, writer: false });
	const choices = startPresetChoices(PRESETS, needs!, 'llm').map((item) => item.preset_id);
	expect(choices).toContain('krea');
	const result = insertStartBefore(definition, 'w', { kind: 'preset', presetId: 'krea', mode: 't2i' }, presets);
	expect(fileEdge(result.definition, 'w')).toMatchObject({ source: result.id, target_handle: 'images' });
});
