import { laneLetters, type Choice, type Lane } from './choice';
import { WRITER_INPUTS, blankNode, checkConnection, choosePreset, connect, isTextEdge, mediaNoun, newId, nodeLabel, nodePorts, removeElements } from './model';

import type { ElixirDefinition, ElixirEdge, ElixirNode, PresetEntry, PresetIndex, Verdict } from './types';

type Def = ElixirDefinition;

export interface Pending {
	from: string | null;
	fromHandle: string | null;
	text: string | null;
}

export interface LaneTree {
	roots: string[];
	children: Map<string, string[]>;
	parent: Map<string, string | null>;
	depth: Map<string, number>;
	columns: number;
}

export interface Slot {
	kind: 'files' | 'prompt';
	port: string | null;
	label: string | undefined;
	media_type?: string | null;
	source: string | null;
	sourceLabel: string | null;
}

export interface MenuItem {
	value: string;
	source: string;
	sourceHandle: string;
	label: string;
	kind: string;
	checked: boolean;
}

export interface SlotChoice extends MenuItem {
	port: string;
	detail: string;
}

export interface Unavailable extends MenuItem {
	reason: string | null;
}

export interface SlotMenu {
	steps: SlotChoice[];
	files: SlotChoice[];
	unavailable: Unavailable[];
}

export interface Chip extends Lane {
	partner?: string;
	partnerTarget?: string;
}

export interface Chips extends Omit<Choice, 'lanes'> {
	lanes: Chip[];
}

const WRITER_PORT: Record<string, string> = Object.fromEntries(Object.entries(WRITER_INPUTS).map(([port, spec]) => [spec.media_type as string, port]));

function writerPort(type: string | null): string | undefined {
	return type === null ? undefined : WRITER_PORT[type];
}

function byId(definition: Def) {
	return new Map(definition.nodes.map((node) => [node.id, node]));
}

export function fileEdge(definition: Def, nodeId: string): ElixirEdge | null {
	return definition.edges.find((edge) => edge.target === nodeId && !isTextEdge(definition, edge)) ?? null;
}

export function promptEdge(definition: Def, nodeId: string): ElixirEdge | null {
	const texts = definition.edges.filter((edge) => edge.target === nodeId && isTextEdge(definition, edge));
	return texts.find((edge) => edge.target_handle === 'prompt') ?? texts[0] ?? null;
}

export function outputType(definition: Def, nodeId: string, handle: string, presetIndex: PresetIndex): string | null {
	const node = definition.nodes.find((item) => item.id === nodeId);
	return node ? nodePorts(node, presetIndex).outputs[handle]?.media_type ?? null : null;
}

export function laneParent(definition: Def, nodeId: string, pending: Record<string, Partial<Pending> | null | undefined> = {}): string | null {
	const waiting = pending[nodeId];
	if (waiting) return waiting.text ?? waiting.from ?? null;
	const text = promptEdge(definition, nodeId);
	if (text) return text.source;
	return fileEdge(definition, nodeId)?.source ?? null;
}

export function laneTree(definition: Def, pending: Record<string, Partial<Pending> | null | undefined> = {}): LaneTree {
	const ids = definition.nodes.map((node) => node.id);
	const known = new Set(ids);
	const parent = new Map<string, string | null>();
	for (const id of ids) {
		const candidate = laneParent(definition, id, pending);
		parent.set(id, candidate && candidate !== id && known.has(candidate) ? candidate : null);
	}
	for (const id of ids) {
		const seen = new Set([id]);
		let current = parent.get(id);
		while (current) {
			if (seen.has(current)) {
				parent.set(id, null);
				break;
			}
			seen.add(current);
			current = parent.get(current);
		}
	}
	const children = new Map<string, string[]>(ids.map((id) => [id, []]));
	const roots: string[] = [];
	for (const id of ids) {
		const up = parent.get(id);
		if (up) (children.get(up) as string[]).push(id);
		else roots.push(id);
	}
	const depth = new Map<string, number>();
	const walk = (id: string, level: number): void => {
		depth.set(id, level);
		for (const child of children.get(id) ?? []) walk(child, level + 1);
	};
	for (const root of roots) walk(root, 0);
	const columns = ids.length ? Math.max(...depth.values()) + 1 : 0;
	return { roots, children, parent, depth, columns };
}

export function descendants(tree: LaneTree, id: string): string[] {
	const found: string[] = [];
	const stack = [...(tree.children.get(id) ?? [])];
	while (stack.length) {
		const next = stack.shift() as string;
		found.push(next);
		stack.push(...(tree.children.get(next) ?? []));
	}
	return found;
}

export function siblingsOf(tree: LaneTree, id: string): string[] {
	const up = tree.parent.get(id);
	return up ? tree.children.get(up) ?? [] : tree.roots;
}

export function writersOf(definition: Def, presetId: string): string[] {
	const nodes = byId(definition);
	return definition.edges
		.filter((edge) => edge.target === presetId && nodes.get(edge.source)?.kind === 'llm')
		.map((edge) => edge.source);
}

export function writerPresets(definition: Def, writerId: string): string[] {
	const nodes = byId(definition);
	return [...new Set(definition.edges.filter((edge) => edge.source === writerId && nodes.get(edge.target)?.kind === 'preset').map((edge) => edge.target))];
}

export function linkedGroup(definition: Def, nodeId: string): string[] {
	const node = definition.nodes.find((item) => item.id === nodeId);
	if (!node) return [];
	if (node.kind === 'llm') return [nodeId, ...writerPresets(definition, nodeId)];
	if (node.kind === 'preset') return [nodeId, ...writersOf(definition, nodeId)];
	return [nodeId];
}

export function cardSlots(definition: Def, nodeId: string, presetIndex: PresetIndex): Slot[] {
	const node = definition.nodes.find((item) => item.id === nodeId);
	if (!node || node.kind === 'media') return [];
	const label = (id: string) => {
		const source = definition.nodes.find((item) => item.id === id);
		return source ? nodeLabel(source, presetIndex) : null;
	};
	if (node.kind === 'llm') {
		const edge = fileEdge(definition, nodeId);
		return [{ kind: 'files', port: edge?.target_handle ?? null, label: 'Looks at', source: edge?.source ?? null, sourceLabel: edge ? label(edge.source) : null }];
	}
	const slots: Slot[] = [];
	for (const [port, spec] of Object.entries(nodePorts(node, presetIndex).inputs)) {
		const edge = definition.edges.find((item) => item.target === nodeId && item.target_handle === port);
		if (spec.media_type === 'text') {
			if (port !== 'prompt' && !edge) continue;
			slots.push({ kind: 'prompt', port, label: spec.label, source: edge?.source ?? null, sourceLabel: edge ? label(edge.source) : null });
			continue;
		}
		slots.push({ kind: 'files', port, label: spec.label, media_type: spec.media_type, source: edge?.source ?? null, sourceLabel: edge ? label(edge.source) : null });
	}
	return slots;
}

function wantedType(node: ElixirNode, port: string | null, presetIndex: PresetIndex): string | null {
	if (node.kind === 'llm') return 'image or video';
	return (port === null ? undefined : nodePorts(node, presetIndex).inputs[port])?.media_type ?? null;
}

function needs(type: string | null): string {
	return type === 'image or video' ? 'images or videos' : mediaNoun(type);
}

export function slotMenu(
	definition: Def,
	nodeId: string,
	port: string | null,
	presetIndex: PresetIndex,
	{ tree = laneTree(definition), estimates = {} }: { tree?: LaneTree; estimates?: Record<string, { outputs?: number } | undefined> } = {}
): SlotMenu {
	const node = definition.nodes.find((item) => item.id === nodeId);
	if (!node) return { steps: [], files: [], unavailable: [] };
	const after = new Set(linkedGroup(definition, nodeId).flatMap((id) => descendants(tree, id)));
	const current = node.kind === 'llm' ? fileEdge(definition, nodeId) : definition.edges.find((edge) => edge.target === nodeId && edge.target_handle === port);
	const steps: SlotChoice[] = [];
	const files: SlotChoice[] = [];
	const unavailable: Unavailable[] = [];
	const type = wantedType(node, port, presetIndex);
	for (const source of definition.nodes) {
		if (source.id === nodeId) continue;
		const outputs = Object.entries(nodePorts(source, presetIndex).outputs);
		for (const [handle, spec] of outputs) {
			const label = outputs.length > 1 ? `${nodeLabel(source, presetIndex)} · ${spec.label}` : nodeLabel(source, presetIndex);
			const item: MenuItem = {
				value: `${source.id}::${handle}`,
				source: source.id,
				sourceHandle: handle,
				label,
				kind: source.kind,
				checked: !!current && current.source === source.id && current.source_handle === handle
			};
			const target = node.kind === 'llm' ? writerPort(spec.media_type) ?? null : port;
			if (after.has(source.id)) {
				unavailable.push({ ...item, reason: 'Comes after this step' });
				continue;
			}
			if (!target || (node.kind !== 'llm' && spec.media_type !== type)) {
				unavailable.push({ ...item, reason: `Makes ${mediaNoun(spec.media_type)}, this step needs ${needs(type)}` });
				continue;
			}
			const verdict = checkConnection(definition, { source: source.id, sourceHandle: handle, target: nodeId, targetHandle: target }, presetIndex);
			if (!verdict.ok) {
				unavailable.push({ ...item, reason: verdict.reason });
				continue;
			}
			const count = estimates?.[source.id]?.outputs ?? 0;
			const detail = [source.kind === 'media' ? 'Files' : source.kind === 'llm' ? 'Prompt writer' : presetLine(source, presetIndex), count > 0 ? `up to ${count}` : null].filter(Boolean).join(' · ');
			(source.kind === 'media' ? files : steps).push({ ...item, port: target, detail });
		}
	}
	return { steps, files, unavailable };
}

function presetLine(node: ElixirNode, presetIndex: PresetIndex): string {
	const preset: PresetEntry | null | undefined = node.preset_id && node.mode ? presetIndex.get(`${node.preset_id}::${node.mode}`) : null;
	return preset ? `${preset.preset_name} · ${preset.mode_label}` : 'No preset chosen';
}

function inputFor(node: ElixirNode, type: string | null, presetIndex: PresetIndex, preferred: string | null = null): string | null {
	if (node.kind === 'llm') return writerPort(type) ?? null;
	const inputs = nodePorts(node, presetIndex).inputs;
	if (preferred && inputs[preferred]?.media_type === type) return preferred;
	return Object.entries(inputs).find(([, spec]) => spec.media_type === type)?.[0] ?? null;
}

function linkFiles(definition: Def, source: string, sourceHandle: string, targetId: string, presetIndex: PresetIndex, preferred: string | null = null): Def {
	const target = definition.nodes.find((item) => item.id === targetId);
	const type = outputType(definition, source, sourceHandle, presetIndex);
	const port = target && type ? inputFor(target, type, presetIndex, preferred) : null;
	if (!port) return definition;
	const result = connect(definition, { source, sourceHandle, target: targetId, targetHandle: port }, presetIndex);
	return result.verdict.ok ? result.definition : definition;
}

export function chooseFileSource(
	definition: Def,
	nodeId: string,
	port: string | null,
	source: string,
	sourceHandle: string,
	presetIndex: PresetIndex
): { definition: Def; verdict: Verdict; moved: string[] } {
	const node = definition.nodes.find((item) => item.id === nodeId);
	if (!node) return { definition, verdict: { ok: false, reason: 'That step no longer exists.' }, moved: [] };
	const type = outputType(definition, source, sourceHandle, presetIndex);
	const target = node.kind === 'llm' ? writerPort(type) : port;
	if (!target) return { definition, verdict: { ok: false, reason: `This step cannot look at ${mediaNoun(type)}.` }, moved: [] };
	const result = connect(definition, { source, sourceHandle, target: nodeId, targetHandle: target }, presetIndex);
	if (!result.verdict.ok) return { ...result, moved: [] };
	let next = result.definition;
	const moved: string[] = [];
	for (const partner of linkedGroup(definition, nodeId).slice(1)) {
		const before = fileEdge(definition, partner);
		const after = linkFiles(next, source, sourceHandle, partner, presetIndex, before?.target_handle ?? null);
		if (after !== next) moved.push(partner);
		next = after;
	}
	return { definition: next, verdict: result.verdict, moved };
}

export function clearFileSource(definition: Def, nodeId: string, port: string): Def {
	const edge = definition.edges.find((item) => item.target === nodeId && item.target_handle === port);
	return edge ? removeElements(definition, [], [edge.id]) : definition;
}

export function promptMenu(definition: Def, presetId: string, presetIndex: PresetIndex): { current: string | null; writers: Array<{ id: string; label: string; checked: boolean }> } {
	const files = fileEdge(definition, presetId);
	const current = promptEdge(definition, presetId)?.source ?? null;
	const same = (writerId: string) => {
		const edge = fileEdge(definition, writerId);
		if (!files || !edge) return !files && !edge;
		return edge.source === files.source && edge.source_handle === files.source_handle;
	};
	const writers = definition.nodes
		.filter((node) => node.kind === 'llm' && (node.id === current || same(node.id)))
		.map((node) => ({ id: node.id, label: nodeLabel(node, presetIndex), checked: node.id === current }));
	return { current, writers };
}

export function choosePromptSource(definition: Def, presetId: string, writerId: string | null, presetIndex: PresetIndex): { definition: Def; removed: string | null; verdict: Verdict } {
	const old = promptEdge(definition, presetId);
	let next = definition;
	if (old && old.source !== writerId) next = removeElements(next, [], [old.id]);
	if (writerId && old?.source !== writerId) {
		const result = connect(next, { source: writerId, sourceHandle: 'text', target: presetId, targetHandle: 'prompt' }, presetIndex);
		if (!result.verdict.ok) return { definition, removed: null, verdict: result.verdict };
		next = result.definition;
	}
	let removed: string | null = null;
	if (old && old.source !== writerId && !next.edges.some((edge) => edge.source === old.source)) {
		removed = old.source;
		next = removeElements(next, [old.source]);
	}
	return { definition: next, removed, verdict: { ok: true, reason: null } };
}

function insertNodeAt(definition: Def, node: ElixirNode, beforeId: string | null = null): Def {
	const nodes = [...definition.nodes];
	const at = beforeId ? nodes.findIndex((item) => item.id === beforeId) : -1;
	if (at === -1) nodes.push(node);
	else nodes.splice(at, 0, node);
	return { ...definition, nodes };
}

export function addWriterFor(definition: Def, presetId: string, presetIndex: PresetIndex): { definition: Def; id: string; removed: string | null } {
	const id = newId('n', definition.nodes.map((node) => node.id));
	let next = insertNodeAt(definition, blankNode('llm', id), presetId);
	const files = fileEdge(definition, presetId);
	if (files) next = linkFiles(next, files.source, files.source_handle, id, presetIndex);
	const result = choosePromptSource(next, presetId, id, presetIndex);
	return { definition: result.definition, id, removed: result.removed };
}

export function addStepAfter(
	definition: Def,
	parentId: string | null,
	presetIndex: PresetIndex,
	{ withWriter = false }: { withWriter?: boolean } = {}
): { definition: Def; id: string; pending: Pending | null; writerId: string | null } {
	const taken = definition.nodes.map((node) => node.id);
	const id = newId('n', taken);
	const parent = parentId ? definition.nodes.find((node) => node.id === parentId) : null;
	let next = { ...definition, nodes: [...definition.nodes, blankNode('preset', id)] };
	if (!parent) return { definition: next, id, pending: null, writerId: null };
	if (parent.kind === 'llm') {
		const files = fileEdge(definition, parent.id);
		return { definition: next, id, pending: { from: files?.source ?? null, fromHandle: files?.source_handle ?? null, text: parent.id }, writerId: null };
	}
	const handle = Object.keys(nodePorts(parent, presetIndex).outputs)[0] ?? null;
	const pending = { from: parent.id, fromHandle: handle, text: null };
	if (!withWriter) return { definition: next, id, pending, writerId: null };
	const writerId = newId('n', [...taken, id]);
	next = insertNodeAt(next, { ...blankNode('llm', writerId), target_node_id: id }, id);
	if (handle) next = linkFiles(next, parent.id, handle, writerId, presetIndex);
	return { definition: next, id, pending: { ...pending, text: writerId }, writerId };
}

export function savedPending(definition: Def): Record<string, Pending> {
	const found: Record<string, Pending> = {};
	for (const node of definition.nodes) {
		if (node.kind !== 'llm' || !node.target_node_id) continue;
		const target = definition.nodes.find((item) => item.id === node.target_node_id);
		if (!target || target.kind !== 'preset' || target.preset_id || found[target.id]) continue;
		if (definition.edges.some((edge) => edge.target === target.id)) continue;
		const files = fileEdge(definition, node.id);
		found[target.id] = { from: files?.source ?? null, fromHandle: files?.source_handle ?? null, text: node.id };
	}
	return found;
}

export function applyPending(definition: Def, nodeId: string, pending: Partial<Pending> | null | undefined, presetIndex: PresetIndex): Def {
	if (!pending) return definition;
	let next = definition;
	if (pending.from && pending.fromHandle) next = linkFiles(next, pending.from, pending.fromHandle, nodeId, presetIndex);
	if (pending.text) {
		const node = next.nodes.find((item) => item.id === nodeId);
		if (node && nodePorts(node, presetIndex).inputs.prompt) {
			const result = connect(next, { source: pending.text, sourceHandle: 'text', target: nodeId, targetHandle: 'prompt' }, presetIndex);
			if (result.verdict.ok) next = result.definition;
		}
	}
	return next;
}

export function choosePendingPreset(definition: Def, nodeId: string, presetId: string | null, mode: string | null, pending: Partial<Pending> | null | undefined, presetIndex: PresetIndex): Def {
	return applyPending(choosePreset(definition, nodeId, presetId, mode), nodeId, pending, presetIndex);
}

export function presetChoices(presets: PresetEntry[] | null | undefined, { takes = null, makes = null, prompt = false }: { takes?: string | null; makes?: string | null; prompt?: boolean } = {}): PresetEntry[] {
	return (presets ?? []).filter((preset) => {
		const inputs = Object.values(preset.inputs ?? {}).map((spec) => spec.media_type);
		const outputs = Object.values(preset.outputs ?? {}).map((spec) => spec.media_type);
		if (outputs.length === 0) return false;
		if (takes && !inputs.includes(takes)) return false;
		if (!takes && !prompt && inputs.length > 0 && !preset.prompt) return false;
		if (makes && !outputs.includes(makes)) return false;
		if (prompt && !preset.prompt) return false;
		return true;
	});
}

export function pendingNeeds(definition: Def, pending: Partial<Pending> | null | undefined, presetIndex: PresetIndex): { takes: string | null; prompt: boolean } {
	if (!pending) return { takes: null, prompt: false };
	const takes = pending.from && pending.fromHandle ? outputType(definition, pending.from, pending.fromHandle, presetIndex) : null;
	return { takes, prompt: !!pending.text };
}

export function insertNeeds(definition: Def, childId: string, presetIndex: PresetIndex): { parent: string; takes: string; makes: string } | null {
	const child = definition.nodes.find((node) => node.id === childId);
	const files = fileEdge(definition, childId);
	if (!child || !files) return null;
	const parent = definition.nodes.find((node) => node.id === files.source);
	if (!parent || parent.kind === 'llm' || (child.kind === 'preset' && writersOf(definition, childId).length > 0)) return null;
	const takes = outputType(definition, files.source, files.source_handle, presetIndex);
	const makes = child.kind === 'llm' ? takes : nodePorts(child, presetIndex).inputs[files.target_handle]?.media_type ?? null;
	return takes && makes ? { parent: files.source, takes, makes } : null;
}

export function insertStepBefore(definition: Def, childId: string, presetId: string, mode: string, presetIndex: PresetIndex): { definition: Def; id: string | null } {
	const needsAt = insertNeeds(definition, childId, presetIndex);
	if (!needsAt) return { definition, id: null };
	const files = fileEdge(definition, childId) as ElixirEdge;
	const id = newId('n', definition.nodes.map((node) => node.id));
	let next = insertNodeAt(definition, { ...blankNode('preset', id), preset_id: presetId, mode }, childId);
	next = linkFiles(next, files.source, files.source_handle, id, presetIndex);
	const node = next.nodes.find((item) => item.id === id) as ElixirNode;
	const handle = Object.entries(nodePorts(node, presetIndex).outputs).find(([, spec]) => spec.media_type === needsAt.makes)?.[0];
	if (!handle) return { definition, id: null };
	for (const member of linkedGroup(definition, childId)) {
		const before = fileEdge(definition, member);
		if (before && before.source === files.source && before.source_handle === files.source_handle) {
			next = linkFiles(next, id, handle, member, presetIndex, before.target_handle);
		}
	}
	return { definition: next, id };
}

export function removeStep(definition: Def, nodeId: string, presetIndex: PresetIndex): { definition: Def; rewired: string[]; to: string | null } {
	const files = fileEdge(definition, nodeId);
	let next = definition;
	const rewired: string[] = [];
	if (files) {
		const type = outputType(definition, files.source, files.source_handle, presetIndex);
		for (const edge of definition.edges) {
			if (edge.source !== nodeId || isTextEdge(definition, edge)) continue;
			const target = definition.nodes.find((node) => node.id === edge.target);
			if (!target) continue;
			const port = target.kind === 'llm' ? writerPort(type) ?? null : nodePorts(target, presetIndex).inputs[edge.target_handle]?.media_type === type ? edge.target_handle : null;
			if (!port) continue;
			next = {
				...next,
				edges: [
					...next.edges.filter((item) => item.id !== edge.id),
					{ id: newId('e', next.edges.map((item) => item.id)), source: files.source, source_handle: files.source_handle, target: edge.target, target_handle: port, iteration: 'each' }
				]
			};
			rewired.push(edge.target);
		}
	}
	return { definition: removeElements(next, [nodeId]), rewired, to: files?.source ?? null };
}

export function moveAmongSiblings(definition: Def, tree: LaneTree, nodeId: string, direction: number): Def {
	const siblings = siblingsOf(tree, nodeId);
	const at = siblings.indexOf(nodeId);
	const other = siblings[at + direction];
	if (at === -1 || !other) return definition;
	const nodes = [...definition.nodes];
	const a = nodes.findIndex((node) => node.id === nodeId);
	const b = nodes.findIndex((node) => node.id === other);
	[nodes[a], nodes[b]] = [nodes[b], nodes[a]];
	return { ...definition, nodes };
}

export function choiceChips(choice: Choice | null | undefined, kindOf: (id: string) => string | null | undefined, order: string[] = []): Chips | null {
	if (!choice) return null;
	const hidden = new Map<string, string>();
	const lanes: Chip[] = [];
	for (const lane of choice.lanes) {
		if (hidden.has(lane.edgeId)) continue;
		const partners = choice.lanes.filter((other) => other.edgeId !== lane.edgeId && lane.linked.includes(other.edgeId));
		if (partners.length === 0) {
			lanes.push(lane);
			continue;
		}
		const members = [lane, ...partners];
		const writer = members.find((item) => kindOf(item.target) === 'llm') ?? lane;
		const preset = (members.find((item) => item !== writer && kindOf(item.target) === 'preset') ?? members.find((item) => item !== writer)) as Lane;
		for (const member of members) hidden.set(member.edgeId, writer.edgeId);
		lanes.push({ ...writer, label: `${preset.label} + writer`, partner: preset.edgeId, partnerTarget: preset.target });
	}
	const rank = (lane: Chip) => {
		const at = order.indexOf(lane.target);
		return at === -1 ? Number.MAX_SAFE_INTEGER : at;
	};
	lanes.sort((a, b) => rank(a) - rank(b));
	const letters = laneLetters(lanes.map((lane) => lane.label));
	const lettered = lanes.map((lane, index) => ({ ...lane, letter: letters[index] }));
	const active = (choice.active ? hidden.get(choice.active) : undefined) ?? choice.active;
	return { ...choice, lanes: lettered, active: lettered.some((lane) => lane.edgeId === active) ? active : lettered.find((lane) => lane.open)?.edgeId ?? null };
}

export function laneOfChild(chips: Chips | null | undefined, childId: string): Chip | null {
	return chips?.lanes.find((lane) => lane.target === childId) ?? null;
}

export interface StartNeeds {
	port: string | null;
	type: string;
	writer: boolean;
	files: boolean;
}

export type StartChoice = { kind: 'preset'; presetId: string; mode: string } | { kind: 'files' } | { kind: 'writer' };

const FEEDABLE = ['image', 'video'];

export function startNeeds(definition: Def, rootId: string, presetIndex: PresetIndex): StartNeeds | null {
	const node = definition.nodes.find((item) => item.id === rootId);
	if (!node || node.kind === 'media') return null;
	if (laneParent(definition, rootId) !== null || fileEdge(definition, rootId) || promptEdge(definition, rootId)) return null;
	if (node.kind === 'llm') return { port: null, type: 'image', writer: false, files: true };
	const inputs = nodePorts(node, presetIndex).inputs;
	const open = Object.entries(inputs).find(
		([port, spec]) => FEEDABLE.includes(spec.media_type ?? '') && !definition.edges.some((edge) => edge.target === rootId && edge.target_handle === port)
	);
	const writer = 'prompt' in inputs && !promptEdge(definition, rootId);
	if (!open && !writer) return null;
	return { port: open?.[0] ?? null, type: open?.[1].media_type ?? 'image', writer, files: !!open };
}

export function startPresetChoices(presets: PresetEntry[] | null | undefined, needs: StartNeeds, rootKind: string): PresetEntry[] {
	if (!needs.files) return [];
	if (rootKind !== 'llm') return presetChoices(presets, { makes: needs.type });
	const seen = new Set<string>();
	return FEEDABLE.flatMap((type) => presetChoices(presets, { makes: type })).filter((preset) => {
		const key = `${preset.preset_id}::${preset.mode}`;
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

export function insertStartBefore(definition: Def, rootId: string, choice: StartChoice, presetIndex: PresetIndex): { definition: Def; id: string | null } {
	const needs = startNeeds(definition, rootId, presetIndex);
	const root = definition.nodes.find((item) => item.id === rootId);
	if (!needs || !root) return { definition, id: null };
	const id = newId('n', definition.nodes.map((node) => node.id));
	if (choice.kind === 'writer') {
		if (!needs.writer) return { definition, id: null };
		const added = insertNodeAt(definition, blankNode('llm', id), rootId);
		const result = connect(added, { source: id, sourceHandle: 'text', target: rootId, targetHandle: 'prompt' }, presetIndex);
		return result.verdict.ok ? { definition: result.definition, id } : { definition, id: null };
	}
	if (!needs.files) return { definition, id: null };
	const node: ElixirNode =
		choice.kind === 'files' ? { ...blankNode('media', id), media_type: needs.type } : { ...blankNode('preset', id), preset_id: choice.presetId, mode: choice.mode };
	const added = insertNodeAt(definition, node, rootId);
	const outputs = Object.entries(nodePorts(node, presetIndex).outputs);
	const exact = outputs.find(([, spec]) => spec.media_type === needs.type);
	const any = root.kind === 'llm' ? outputs.find(([, spec]) => FEEDABLE.includes(spec.media_type ?? '')) : undefined;
	const handle = (exact ?? any)?.[0];
	if (!handle) return { definition, id: null };
	const linked = linkFiles(added, id, handle, rootId, presetIndex, needs.port);
	return linked.edges.length > added.edges.length ? { definition: linked, id } : { definition, id: null };
}
