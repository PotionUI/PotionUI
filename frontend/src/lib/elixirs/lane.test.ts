import { expect, test } from 'vitest';
import { indexPresets } from './model';
import {
	addStepAfter,
	addWriterFor,
	cardSlots,
	chooseFileSource,
	choosePendingPreset,
	choosePromptSource,
	choiceChips,
	clearFileSource,
	descendants,
	fileEdge,
	insertNeeds,
	insertStepBefore,
	laneOfChild,
	laneTree,
	moveAmongSiblings,
	pendingNeeds,
	presetChoices,
	promptEdge,
	promptMenu,
	removeStep,
	savedPending,
	slotMenu
} from './lane';
import type { Choice, Lane } from './choice';
import type { ElixirDefinition, ElixirEdge, ElixirNode, PresetEntry } from './types';

const PRESETS: PresetEntry[] = [
	{ preset_id: 'krea', preset_name: 'Krea-2', mode: 't2i', mode_label: 'Text to image', prompt: true, inputs: {}, outputs: { images: { label: 'Images', media_type: 'image' } } },
	{ preset_id: 'wan', preset_name: 'Wan', mode: 'i2v', mode_label: 'Image to video', prompt: true, inputs: { first_frame: { label: 'First frame', media_type: 'image' } }, outputs: { videos: { label: 'Videos', media_type: 'video' } } },
	{ preset_id: 'seed', preset_name: 'SeedVR2', mode: 'vup', mode_label: 'Video upscale', prompt: false, inputs: { video: { label: 'Video', media_type: 'video' } }, outputs: { videos: { label: 'Videos', media_type: 'video' } } },
	{ preset_id: 'qwen', preset_name: 'Qwen', mode: 'edit', mode_label: 'Image edit', prompt: true, inputs: { reference: { label: 'Reference image', media_type: 'image' } }, outputs: { images: { label: 'Images', media_type: 'image' } } }
];
const presets = indexPresets(PRESETS);

function preset(id: string, presetId: string, mode: string, title: string): ElixirNode {
	return { id, kind: 'preset', title, preset_id: presetId, mode, overrides: {}, session_source: null, session_snapshot: null };
}

function edge(id: string, source: string, source_handle: string, target: string, target_handle: string): ElixirEdge {
	return { id, source, source_handle, target, target_handle, iteration: 'each' };
}

function study(): ElixirDefinition {
	return {
		schema_version: 1,
		viewport: null,
		nodes: [
			preset('photo', 'krea', 't2i', 'Product photo'),
			preset('orbit', 'wan', 'i2v', 'Orbit left'),
			preset('up', 'seed', 'vup', 'Upscale clips'),
			{ id: 'writer', kind: 'llm', title: 'Describe the move', instruction: 'Describe a move', target_node_id: null, pinned: {} },
			preset('close', 'wan', 'i2v', 'Close-up reveal'),
			{ id: 'stills', kind: 'media', title: 'Brand stills', media_type: 'image', assets: [] },
			preset('gold', 'qwen', 'edit', 'Gold restyle')
		],
		edges: [
			edge('e1', 'photo', 'images', 'orbit', 'first_frame'),
			edge('e2', 'orbit', 'videos', 'up', 'video'),
			edge('e3', 'photo', 'images', 'writer', 'images'),
			edge('e4', 'photo', 'images', 'close', 'first_frame'),
			edge('e5', 'writer', 'text', 'close', 'prompt'),
			edge('e6', 'stills', 'images', 'gold', 'reference')
		]
	};
}

test('the lane is a tree: a preset with a writer sits under the writer, the writer under its files', () => {
	const tree = laneTree(study());
	expect(tree.roots).toStrictEqual(['photo', 'stills']);
	expect(tree.children.get('photo')).toStrictEqual(['orbit', 'writer']);
	expect(tree.children.get('writer')).toStrictEqual(['close']);
	expect(tree.parent.get('close')).toBe('writer');
	expect(tree.parent.get('writer')).toBe('photo');
	expect([tree.depth.get('photo'), tree.depth.get('close'), tree.depth.get('up'), tree.depth.get('gold')]).toStrictEqual([0, 2, 2, 1]);
	expect(tree.columns).toBe(3);
	expect(descendants(tree, 'photo')).toStrictEqual(['orbit', 'writer', 'up', 'close']);
});

test('a preset fed only by a writer sits under that writer, and the writer with no files is a start', () => {
	const definition: ElixirDefinition = {
		...study(),
		nodes: [...study().nodes, { id: 'solo', kind: 'llm', title: 'Ideas', instruction: 'x', pinned: {} }, preset('t2i', 'krea', 't2i', 'From ideas')],
		edges: [...study().edges, edge('e7', 'solo', 'text', 't2i', 'prompt')]
	};
	const tree = laneTree(definition);
	expect(tree.parent.get('t2i')).toBe('solo');
	expect(tree.roots.includes('solo')).toBeTruthy();
	expect(!tree.roots.includes('t2i')).toBeTruthy();
});

test('a step waiting for its preset is placed by its pending parent', () => {
	const { definition, id, pending } = addStepAfter(study(), 'orbit', presets);
	const tree = laneTree(definition, { [id]: pending });
	expect(tree.children.get('orbit')).toStrictEqual(['up', id]);
	expect(laneTree(definition).roots.includes(id)).toBeTruthy();
});

test('a loop in the data never hangs and still shows every card', () => {
	const definition = {
		...study(),
		edges: [...study().edges, edge('loop', 'up', 'videos', 'photo', 'first_frame')]
	};
	const tree = laneTree(definition);
	expect(tree.parent.size).toBe(definition.nodes.length);
	const reachable = new Set(tree.roots.flatMap((root) => [root, ...descendants(tree, root)]));
	expect(reachable.size).toBe(definition.nodes.length);
});

test('fan-out branches follow the node order, and moving a step swaps it with its sibling only', () => {
	const definition = study();
	const moved = moveAmongSiblings(definition, laneTree(definition), 'writer', -1);
	expect(laneTree(moved).children.get('photo')).toStrictEqual(['writer', 'orbit']);
	expect(moveAmongSiblings(definition, laneTree(definition), 'orbit', -1)).toBe(definition);
	expect(moveAmongSiblings(definition, laneTree(definition), 'writer', 1)).toBe(definition);
	const roots = moveAmongSiblings(definition, laneTree(definition), 'stills', -1);
	expect(laneTree(roots).roots).toStrictEqual(['stills', 'photo']);
});

test('card slots list the file inputs, the prompt, and what a writer looks at', () => {
	const definition = study();
	expect(cardSlots(definition, 'close', presets).map((slot) => [slot.kind, slot.label, slot.sourceLabel])).toStrictEqual([
			['files', 'First frame', 'Product photo'],
			['prompt', 'Prompt', 'Describe the move']
		]);
	expect(cardSlots(definition, 'writer', presets).map((slot) => [slot.label, slot.sourceLabel, slot.port])).toStrictEqual([['Looks at', 'Product photo', 'images']]);
	expect(cardSlots(definition, 'up', presets).map((slot) => slot.kind)).toStrictEqual(['files']);
	expect(cardSlots(definition, 'stills', presets)).toStrictEqual([]);
});

test('a file slot offers steps and files, and says why the others are not available', () => {
	const menu = slotMenu(study(), 'orbit', 'first_frame', presets, { estimates: { photo: { outputs: 3 } } });
	expect(menu.steps.map((item) => [item.source, item.checked, item.detail])).toStrictEqual([
		['photo', true, 'Krea-2 · Text to image · up to 3'],
		['gold', false, 'Qwen · Image edit']
	]);
	expect(menu.files.map((item) => item.source)).toStrictEqual(['stills']);
	expect(Object.fromEntries(menu.unavailable.map((item) => [item.source, item.reason]))).toStrictEqual({
		up: 'Comes after this step',
		writer: 'Makes prompts, this step needs images',
		close: 'Makes videos, this step needs images'
	});
});

test('a writer cannot look at the preset it writes for or anything after it', () => {
	const menu = slotMenu(study(), 'writer', null, presets);
	const reasons = Object.fromEntries(menu.unavailable.map((item) => [item.source, item.reason]));
	expect(reasons.close).toBe('Comes after this step');
	expect(menu.files.map((item) => [item.source, item.port])).toStrictEqual([['stills', 'images']]);
	expect(menu.steps.some((item) => item.source === 'orbit' && item.port === 'videos')).toBeTruthy();
});

test('changing a preset\'s files moves its writer, and changing the writer moves its preset', () => {
	const first = chooseFileSource(study(), 'close', 'first_frame', 'stills', 'images', presets);
	expect(first.verdict.ok).toBeTruthy();
	expect(first.moved).toStrictEqual(['writer']);
	expect(fileEdge(first.definition, 'close')!.source).toBe('stills');
	expect(fileEdge(first.definition, 'writer')!.source).toBe('stills');
	expect(promptEdge(first.definition, 'close')!.source).toBe('writer');

	const second = chooseFileSource(study(), 'writer', null, 'gold', 'images', presets);
	expect(second.moved).toStrictEqual(['close']);
	expect([fileEdge(second.definition, 'writer')!.source, fileEdge(second.definition, 'close')!.source]).toStrictEqual(['gold', 'gold']);
	expect(second.definition.edges.filter((item) => item.target === 'close').length).toBe(2);
	expect(laneTree(second.definition).parent.get('writer')).toBe('gold');
});

test('a refused source leaves the elixir as it was', () => {
	const base = study();
	const result = chooseFileSource(base, 'orbit', 'first_frame', 'up', 'videos', presets);
	expect(result.verdict.ok).toBe(false);
	expect(result.definition).toBe(base);
	expect(fileEdge(result.definition, 'orbit')!.source).toBe('photo');
});

test('a file slot can go back to the session file', () => {
	const cleared = clearFileSource(study(), 'gold', 'reference');
	expect(fileEdge(cleared, 'gold')).toBe(null);
	expect(laneTree(cleared).roots.includes('gold')).toBeTruthy();
});

test('the prompt slot lists writers that read the same files and removes an unused writer', () => {
	const menu = promptMenu(study(), 'orbit', presets);
	expect(menu.writers.map((item) => [item.id, item.checked])).toStrictEqual([['writer', false]]);
	expect(promptMenu(study(), 'gold', presets).writers).toStrictEqual([]);

	const reused = choosePromptSource(study(), 'orbit', 'writer', presets);
	expect(promptEdge(reused.definition, 'orbit')!.source).toBe('writer');
	const back = choosePromptSource(reused.definition, 'close', null, presets);
	expect(back.removed).toBe(null);
	expect(back.definition.nodes.some((node) => node.id === 'writer')).toBeTruthy();

	const gone = choosePromptSource(study(), 'close', null, presets);
	expect(gone.removed).toBe('writer');
	expect(promptEdge(gone.definition, 'close')).toBe(null);
	expect(!gone.definition.nodes.some((node) => node.id === 'writer')).toBeTruthy();
	expect(laneTree(gone.definition).parent.get('close')).toBe('photo');
});

test('write with AI adds a writer before the preset that reads the same files', () => {
	const { definition, id } = addWriterFor(study(), 'orbit', presets);
	const writer = definition.nodes.find((node) => node.id === id)!;
	expect(writer.kind).toBe('llm');
	expect([fileEdge(definition, id)!.source, fileEdge(definition, id)!.target_handle]).toStrictEqual(['photo', 'images']);
	expect(promptEdge(definition, 'orbit')!.source).toBe(id);
	expect(laneTree(definition).parent.get('orbit')).toBe(id);
	expect(definition.nodes.findIndex((node) => node.id === id) < definition.nodes.findIndex((node) => node.id === 'orbit')).toBeTruthy();
});

test('a next step waits for its preset, then connects from the step before it', () => {
	const added = addStepAfter(study(), 'photo', presets);
	expect(added.pending).toStrictEqual({ from: 'photo', fromHandle: 'images', text: null });
	expect(pendingNeeds(added.definition, added.pending, presets)).toStrictEqual({ takes: 'image', prompt: false });
	const chosen = choosePendingPreset(added.definition, added.id, 'wan', 'i2v', added.pending, presets);
	expect([fileEdge(chosen, added.id)!.source, fileEdge(chosen, added.id)!.target_handle]).toStrictEqual(['photo', 'first_frame']);
	expect(laneTree(chosen).parent.get(added.id)).toBe('photo');
});

test('a next step after a writer takes its prompt from it and its files from the writer\'s source', () => {
	const added = addStepAfter(study(), 'writer', presets);
	expect(added.pending).toStrictEqual({ from: 'photo', fromHandle: 'images', text: 'writer' });
	const chosen = choosePendingPreset(added.definition, added.id, 'qwen', 'edit', added.pending, presets);
	expect(fileEdge(chosen, added.id)!.source).toBe('photo');
	expect(promptEdge(chosen, added.id)!.source).toBe('writer');
	expect(laneTree(chosen).children.get('writer')).toStrictEqual(['close', added.id]);
});

test('a step with a prompt writer adds the writer reading the parent first', () => {
	const added = addStepAfter(study(), 'gold', presets, { withWriter: true });
	expect(added.writerId).toBeTruthy();
	expect(fileEdge(added.definition, added.writerId!)!.source).toBe('gold');
	expect(laneTree(added.definition, { [added.id]: added.pending }).children.get(added.writerId!)).toStrictEqual([added.id]);
	const chosen = choosePendingPreset(added.definition, added.id, 'wan', 'i2v', added.pending, presets);
	expect(promptEdge(chosen, added.id)!.source).toBe(added.writerId);
	expect(fileEdge(chosen, added.id)!.source).toBe('gold');
});

test('a step added with a writer keeps its writer and waiting state after the definition is saved and reloaded', () => {
	const added = addStepAfter(study(), 'gold', presets, { withWriter: true });
	const reloaded = JSON.parse(JSON.stringify(added.definition));
	const waiting = savedPending(reloaded);
	expect(waiting[added.id]).toStrictEqual({ from: 'gold', fromHandle: 'images', text: added.writerId });
	expect(laneTree(reloaded, waiting).children.get(added.writerId!)).toStrictEqual([added.id]);
	expect(pendingNeeds(reloaded, waiting[added.id], presets)).toStrictEqual({ takes: 'image', prompt: true });
});

test('a chosen preset no longer counts as waiting', () => {
	const added = addStepAfter(study(), 'gold', presets, { withWriter: true });
	const chosen = choosePendingPreset(added.definition, added.id, 'wan', 'i2v', added.pending, presets);
	expect(savedPending(chosen)).toStrictEqual({});
});

test('a start has no pending parent', () => {
	const added = addStepAfter(study(), null, presets);
	expect(added.pending).toBe(null);
	expect(laneTree(added.definition).roots.includes(added.id)).toBeTruthy();
});

test('the preset chooser lists only presets that take what comes in', () => {
	expect(presetChoices(PRESETS, { takes: 'video' }).map((item) => item.preset_id)).toStrictEqual(['seed']);
	expect(presetChoices(PRESETS, { takes: 'image' }).map((item) => item.preset_id)).toStrictEqual(['wan', 'qwen']);
	expect(presetChoices(PRESETS, { takes: 'image', prompt: true }).map((item) => item.preset_id)).toStrictEqual(['wan', 'qwen']);
	expect(presetChoices(PRESETS, {}).map((item) => item.preset_id)).toStrictEqual(['krea', 'wan', 'qwen']);
	expect(presetChoices(PRESETS, { takes: 'image', makes: 'image' }).map((item) => item.preset_id)).toStrictEqual(['qwen']);
});

test('inserting a step between two steps rewires both sides', () => {
	expect(insertNeeds(study(), 'orbit', presets)).toStrictEqual({ parent: 'photo', takes: 'image', makes: 'image' });
	const { definition, id } = insertStepBefore(study(), 'orbit', 'qwen', 'edit', presets);
	expect(fileEdge(definition, id!)!.source).toBe('photo');
	expect(fileEdge(definition, 'orbit')!.source).toBe(id);
	expect(laneTree(definition).children.get(id!)).toStrictEqual(['orbit']);
});

test('inserting before a writer moves the writer and its preset together', () => {
	const { definition, id } = insertStepBefore(study(), 'writer', 'qwen', 'edit', presets);
	expect(fileEdge(definition, 'writer')!.source).toBe(id);
	expect(fileEdge(definition, 'close')!.source).toBe(id);
	expect(promptEdge(definition, 'close')!.source).toBe('writer');
	expect(insertNeeds(study(), 'close', presets)).toBe(null);
	expect(insertNeeds(study(), 'photo', presets)).toBe(null);
});

test('removing a step hands its files to the steps after it when they fit', () => {
	const chain = {
		...study(),
		nodes: [...study().nodes, preset('again', 'qwen', 'edit', 'Again')],
		edges: [...study().edges, edge('e8', 'gold', 'images', 'again', 'reference')]
	};
	const removed = removeStep(chain, 'gold', presets);
	expect(removed.rewired).toStrictEqual(['again']);
	expect(fileEdge(removed.definition, 'again')!.source).toBe('stills');
	expect(!removed.definition.nodes.some((node) => node.id === 'gold')).toBeTruthy();

	const mismatch = removeStep(study(), 'orbit', presets);
	expect(mismatch.rewired).toStrictEqual([]);
	expect(fileEdge(mismatch.definition, 'up')).toBe(null);

	const writer = removeStep(study(), 'writer', presets);
	expect(promptEdge(writer.definition, 'close')).toBe(null);
	expect(fileEdge(writer.definition, 'close')!.source).toBe('photo');
});

function lane(edgeId: string, target: string, label: string, extra: Partial<Lane> = {}): Lane {
	return { edgeId, target, label, letter: label[0], open: true, sent: false, selected: [], eligible: ['a', 'b', 'c'], linked: [], ...extra } as Lane;
}

test('a writer and its preset are one choice on the writer, ordered like the cards', () => {
	const choice: Choice = {
		lanes: [
			lane('e4', 'close', 'Close-up reveal', { linked: ['e3'] }),
			lane('e1', 'orbit', 'Orbit left'),
			lane('e3', 'writer', 'Describe the move', { linked: ['e4'] })
		],
		active: 'e4',
		choosing: true
	};
	const kinds: Record<string, string> = { close: 'preset', orbit: 'preset', writer: 'llm' };
	const chips = choiceChips(choice, (id) => kinds[id], ['orbit', 'writer'])!;
	expect(chips.lanes.map((item) => [item.edgeId, item.label, item.letter])).toStrictEqual([
		['e1', 'Orbit left', 'O'],
		['e3', 'Close-up reveal + writer', 'C']
	]);
	expect(chips.active).toBe('e3');
	expect(laneOfChild(chips, 'writer')!.partner).toBe('e4');
	expect(laneOfChild(chips, 'close')).toBe(null);
	expect(choiceChips(null, () => null)).toBe(null);
});
