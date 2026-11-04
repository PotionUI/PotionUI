import type {
	Connection,
	ElixirDefinition,
	ElixirEdge,
	ElixirNode,
	MediaAsset,
	NodeKind,
	NodeLike,
	NodeOverrides,
	PortMap,
	PresetEntry,
	PresetIndex,
	SessionSettings,
	ValidationIssue,
	ValidationReport,
	Verdict
} from './types';

export const MEDIA_PORTS: Record<string, string> = { image: 'images', video: 'videos' };

export const MEDIA_LABELS: Record<string, string> = { image: 'Images', video: 'Videos' };

export const SETUP_CODES = new Set(['no_media', 'no_preset', 'no_session', 'media_type_missing', 'no_instruction']);

export const WRITER_INPUTS: PortMap = {
	images: { label: 'Images', media_type: 'image' },
	videos: { label: 'Videos', media_type: 'video' }
};

export const WRITER_OUTPUTS: PortMap = { text: { label: 'Text', media_type: 'text' } };

export const PROMPT_INPUTS: Record<string, string> = { prompt: 'Prompt', negative_prompt: 'Negative prompt' };

const KIND_NOUNS: Record<string, string> = { media: 'media source', preset: 'preset node', llm: 'prompt writer' };

const UNTITLED: Record<string, string> = { media: 'Untitled media source', llm: 'Untitled prompt writer' };

export interface Ports {
	inputs: PortMap;
	outputs: PortMap;
}

export interface PickedItem {
	key?: string;
	origin?: { kind?: string; generationId?: unknown; fileId?: unknown; itemId?: unknown } | null;
	mediaType?: string | null;
	filename?: string | null;
}

export interface SnapshotSummary {
	prompt: string;
	negative: string;
	seed: unknown;
	variant: string | null;
	director: { mode: string | null } | null;
	variables: number;
	settings: Array<{ name: string; value: string }>;
	media: string[];
	edited: boolean;
}

export interface IssueGroups {
	nodes: Map<string, ValidationIssue[]>;
	edges: Map<string, ValidationIssue[]>;
	general: ValidationIssue[];
}

export function kindNoun(kind: string | null | undefined): string {
	return KIND_NOUNS[kind ?? ''] ?? 'node';
}

export function mediaNoun(mediaType: string | null | undefined, count = 2): string {
	if (mediaType === 'text') return count === 1 ? 'prompt' : 'prompts';
	if (mediaType === 'video') return count === 1 ? 'video' : 'videos';
	return count === 1 ? 'image' : 'images';
}

export function newId(prefix: string, existing: Iterable<string> = []): string {
	const taken = new Set(existing);
	for (;;) {
		const id = `${prefix}${Math.random().toString(36).slice(2, 8)}`;
		if (!taken.has(id)) return id;
	}
}

export function emptyDefinition(): ElixirDefinition {
	return { schema_version: 1, nodes: [], edges: [], viewport: null };
}

export function presetKey(presetId: string | null | undefined, mode: string | null | undefined): string {
	return `${presetId}::${mode}`;
}

export function indexPresets(presets: PresetEntry[] | null | undefined): PresetIndex {
	const index: PresetIndex = new Map();
	for (const preset of presets || []) index.set(presetKey(preset.preset_id, preset.mode), preset);
	return index;
}

function promptInputs(preset: PresetEntry | null | undefined): PortMap {
	if (!preset?.prompt) return {};
	const taken = new Set([...Object.keys(preset.inputs ?? {}), ...Object.keys(preset.outputs ?? {})]);
	return Object.fromEntries(
		Object.entries(PROMPT_INPUTS)
			.filter(([name]) => !taken.has(name))
			.map(([name, label]) => [name, { label, media_type: 'text', target: 'prompt' }])
	);
}

function presetOf(node: NodeLike, presetIndex: PresetIndex): PresetEntry | null {
	return (node.preset_id && node.mode ? presetIndex.get(presetKey(node.preset_id, node.mode)) : null) ?? null;
}

export function nodePorts(node: NodeLike, presetIndex: PresetIndex): Ports {
	if (node.kind === 'llm') return { inputs: WRITER_INPUTS, outputs: WRITER_OUTPUTS };
	if (node.kind === 'media') {
		const mediaType = node.media_type ?? '';
		const port = MEDIA_PORTS[mediaType];
		return {
			inputs: {},
			outputs: port ? { [port]: { label: MEDIA_LABELS[mediaType], media_type: mediaType } } : {}
		};
	}
	const preset = presetOf(node, presetIndex);
	return { inputs: { ...(preset?.inputs ?? {}), ...promptInputs(preset) }, outputs: preset?.outputs ?? {} };
}

export function isTextEdge(definition: ElixirDefinition, edge: Pick<ElixirEdge, 'source'>): boolean {
	return definition.nodes.find((node) => node.id === edge.source)?.kind === 'llm';
}

export function writerTarget(definition: ElixirDefinition, node: ElixirNode): string | null {
	const byId = new Map(definition.nodes.map((item) => [item.id, item]));
	if (node.target_node_id) return byId.get(node.target_node_id)?.kind === 'preset' ? node.target_node_id : null;
	const edge = definition.edges.find((item) => item.source === node.id && byId.get(item.target)?.kind === 'preset');
	return edge ? edge.target : null;
}

export function promptConnected(definition: ElixirDefinition, nodeId: string): boolean {
	return definition.edges.some((edge) => edge.target === nodeId && edge.target_handle === 'prompt' && isTextEdge(definition, edge));
}

export function nodeLabel(node: NodeLike, presetIndex: PresetIndex): string {
	const title = (node.title || '').trim();
	if (title) return title;
	if (node.kind && UNTITLED[node.kind]) return UNTITLED[node.kind];
	const preset = presetOf(node, presetIndex);
	return preset ? `${preset.preset_name} · ${preset.mode_label}` : 'Untitled preset node';
}

function reaches(edges: ElixirEdge[], from: string, to: string): boolean {
	const next = new Map<string, string[]>();
	for (const edge of edges) {
		if (!next.has(edge.source)) next.set(edge.source, []);
		(next.get(edge.source) as string[]).push(edge.target);
	}
	const seen = new Set<string>();
	const stack = [from];
	while (stack.length) {
		const current = stack.pop() as string;
		if (current === to) return true;
		if (seen.has(current)) continue;
		seen.add(current);
		stack.push(...(next.get(current) ?? []));
	}
	return false;
}

export function checkConnection(definition: ElixirDefinition, connection: Connection, presetIndex: PresetIndex): Verdict {
	const { source, target, sourceHandle, targetHandle } = connection;
	const byId = new Map(definition.nodes.map((node) => [node.id, node]));
	const from = byId.get(source);
	const to = byId.get(target);
	if (!from || !to) return { ok: false, reason: 'That node no longer exists.' };
	if (source === target) return { ok: false, reason: 'A node cannot feed into itself.' };
	if (to.kind === 'media') return { ok: false, reason: 'A media source cannot receive a connection.' };
	const output = nodePorts(from, presetIndex).outputs[sourceHandle];
	const input = nodePorts(to, presetIndex).inputs[targetHandle];
	if (!output || !input) return { ok: false, reason: 'Those ports do not exist.' };
	if (output.media_type !== input.media_type) {
		return { ok: false, reason: `${input.label} takes ${mediaNoun(input.media_type)}, not ${mediaNoun(output.media_type)}.` };
	}
	const others = definition.edges.filter((edge) => edge.target !== target);
	if (reaches(others, target, source)) return { ok: false, reason: 'That connection would make a loop.' };
	return { ok: true, reason: null };
}

export function connect(definition: ElixirDefinition, connection: Connection, presetIndex: PresetIndex): { definition: ElixirDefinition; verdict: Verdict } {
	const verdict = checkConnection(definition, connection, presetIndex);
	if (!verdict.ok) return { definition, verdict };
	const text = definition.nodes.find((node) => node.id === connection.source)?.kind === 'llm';
	const edges = definition.edges.filter(
		(edge) =>
			edge.target !== connection.target ||
			(edge.target_handle !== connection.targetHandle && (text || isTextEdge(definition, edge)))
	);
	edges.push({
		id: newId('e', definition.edges.map((edge) => edge.id)),
		source: connection.source,
		source_handle: connection.sourceHandle,
		target: connection.target,
		target_handle: connection.targetHandle,
		iteration: 'each'
	});
	return { definition: { ...definition, edges }, verdict };
}

export function removeElements(definition: ElixirDefinition, nodeIds: string[] = [], edgeIds: string[] = []): ElixirDefinition {
	const nodes = new Set(nodeIds);
	const edges = new Set(edgeIds);
	return {
		...definition,
		nodes: definition.nodes.filter((node) => !nodes.has(node.id)),
		edges: definition.edges.filter(
			(edge) => !edges.has(edge.id) && !nodes.has(edge.source) && !nodes.has(edge.target)
		)
	};
}

export function blankNode(kind: NodeKind, id: string): ElixirNode {
	if (kind === 'llm') return { id, kind: 'llm', title: null, instruction: '', target_node_id: null, pinned: {} };
	if (kind === 'media') return { id, kind: 'media', title: null, media_type: null, assets: [], source_revision: 0 };
	return {
		id,
		kind: 'preset',
		title: null,
		preset_id: null,
		mode: null,
		form_name: null,
		overrides: {},
		session_source: null,
		session_snapshot: null
	};
}

export function addNode(definition: ElixirDefinition, kind: NodeKind): { definition: ElixirDefinition; id: string } {
	const id = newId('n', definition.nodes.map((node) => node.id));
	return { definition: { ...definition, nodes: [...definition.nodes, blankNode(kind, id)] }, id };
}

export function updateNode(definition: ElixirDefinition, id: string, changes: Partial<ElixirNode>): ElixirDefinition {
	return {
		...definition,
		nodes: definition.nodes.map((node) => (node.id === id ? { ...node, ...changes } : node))
	};
}

export function choosePreset(definition: ElixirDefinition, id: string, presetId: string | null, mode: string | null): ElixirDefinition {
	const node = definition.nodes.find((item) => item.id === id);
	if (!node || (node.preset_id === presetId && node.mode === mode)) return definition;
	return updateNode(definition, id, {
		preset_id: presetId,
		mode,
		form_name: null,
		overrides: {},
		session_source: null,
		session_snapshot: null
	});
}

type AssetLike = Pick<MediaAsset, 'kind' | 'generation_id' | 'file_id' | 'item_id'>;

export function assetKey(asset: AssetLike): string | undefined {
	return asset.kind === 'history' ? `${asset.generation_id}:${asset.file_id}` : asset.item_id;
}

export function pickerKeys(assets: MediaAsset[] | null | undefined, kind: string): Array<string | undefined> {
	return (assets || []).filter((asset) => asset.kind === kind).map(assetKey);
}

function refFromItem(item: PickedItem): AssetLike | null {
	const origin = item.origin || {};
	if (origin.kind === 'history') {
		return { kind: 'history', generation_id: String(origin.generationId), file_id: String(origin.fileId) };
	}
	if (origin.kind === 'library') return { kind: 'library', item_id: String(origin.itemId) };
	return null;
}

export function mergePicked(assets: MediaAsset[] | null | undefined, kind: string, items: PickedItem[] | null | undefined): MediaAsset[] {
	const existing = new Map((assets || []).filter((asset) => asset.kind === kind).map((asset) => [assetKey(asset), asset]));
	const picked: MediaAsset[] = [];
	for (const item of items || []) {
		const ref = refFromItem(item);
		if (!ref || ref.kind !== kind) continue;
		const known = existing.get(assetKey(ref));
		picked.push(
			known ?? {
				...ref,
				media_type: item.mediaType ?? null,
				original_filename: item.filename ?? null,
				preview_url: null
			}
		);
	}
	const pickedKeys = new Set(picked.map(assetKey));
	const kept = (assets || []).filter((asset) => asset.kind !== kind || pickedKeys.has(assetKey(asset)));
	const keptKeys = new Set(kept.filter((asset) => asset.kind === kind).map(assetKey));
	return [...kept, ...picked.filter((asset) => !keptKeys.has(assetKey(asset)))];
}

export function mediaTypeOf(assets: Array<Pick<MediaAsset, 'media_type'>> | null | undefined): string | null {
	const types = new Set((assets || []).map((asset) => asset.media_type).filter(Boolean));
	return types.size === 1 ? ([...types][0] as string) : null;
}

function sameKeys(left: MediaAsset[] | null | undefined, right: MediaAsset[] | null | undefined): boolean {
	const a = (left || []).map((asset) => `${asset.kind}:${assetKey(asset)}`);
	const b = (right || []).map((asset) => `${asset.kind}:${assetKey(asset)}`);
	return a.length === b.length && a.every((key, index) => key === b[index]);
}

type NodeSet = Pick<ElixirDefinition, 'nodes'>;

export function mergeSaved<T extends NodeSet>(local: T, sent: NodeSet, saved: NodeSet): T {
	const savedById = new Map(saved.nodes.map((node) => [node.id, node]));
	const sentById = new Map(sent.nodes.map((node) => [node.id, node]));
	return {
		...local,
		nodes: local.nodes.map((node) => {
			const server = savedById.get(node.id);
			const outgoing = sentById.get(node.id);
			if (!server || !outgoing || server.kind !== node.kind || node.kind === 'llm') return node;
			if (node.kind === 'preset') {
				if (node.preset_id !== server.preset_id || node.mode !== server.mode) return node;
				return {
					...node,
					session_source: server.session_source,
					session_snapshot: server.session_snapshot,
					session_mode: server.session_mode,
					session_ref: server.session_ref,
					session_hash: server.session_hash,
					form_name: node.form_name === outgoing.form_name ? server.form_name : node.form_name
				};
			}
			if (!sameKeys(node.assets, server.assets)) return node;
			return {
				...node,
				assets: server.assets,
				media_type: node.media_type === outgoing.media_type ? server.media_type : node.media_type,
				source_revision: server.source_revision
			};
		})
	};
}

export function issuesByTarget(report: ValidationReport | null | undefined): IssueGroups {
	const nodes = new Map<string, ValidationIssue[]>();
	const edges = new Map<string, ValidationIssue[]>();
	const general: ValidationIssue[] = [];
	for (const issue of report?.issues ?? []) {
		if (issue.edge_id) {
			if (!edges.has(issue.edge_id)) edges.set(issue.edge_id, []);
			(edges.get(issue.edge_id) as ValidationIssue[]).push(issue);
		}
		if (issue.node_id) {
			if (!nodes.has(issue.node_id)) nodes.set(issue.node_id, []);
			(nodes.get(issue.node_id) as ValidationIssue[]).push(issue);
		}
		if (!issue.edge_id && !issue.node_id) general.push(issue);
	}
	return { nodes, edges, general };
}

export function worstSeverity(issues: ValidationIssue[] | null | undefined): 'setup' | 'error' | 'warning' | null {
	if (!issues || issues.length === 0) return null;
	if (issues.every((issue) => SETUP_CODES.has(issue.code))) return 'setup';
	return issues.some((issue) => issue.severity === 'error') ? 'error' : 'warning';
}

function text(value: unknown): string {
	return typeof value === 'string' ? value.trim() : '';
}

function segmentsText(segments: SessionSettings['promptSegments']): string {
	if (!Array.isArray(segments)) return '';
	return segments
		.map((segment) => text(segment?.content ?? segment?.text))
		.filter(Boolean)
		.join(' ');
}

function scalar(value: unknown): boolean {
	return value === null || ['string', 'number', 'boolean'].includes(typeof value);
}

export function summarizeSnapshot(snapshot: SessionSettings | null | undefined, overrides: NodeOverrides = {}): SnapshotSummary | null {
	if (!snapshot) return null;
	const formData: Record<string, unknown> = { ...(snapshot.formData || {}), ...(overrides.formData || {}) };
	const effective: SessionSettings = { ...snapshot, ...overrides, formData };
	const prompt = segmentsText(effective.promptSegments) || text(effective.prompt);
	const negative = segmentsText(effective.negativePromptSegments) || text(effective.negativePrompt);
	const director = effective.videoDirector && typeof effective.videoDirector === 'object' ? effective.videoDirector : null;
	const settings = Object.entries(formData)
		.filter(([, value]) => scalar(value) && value !== '' && value !== null)
		.map(([name, value]) => ({ name, value: String(value) }))
		.sort((a, b) => a.name.localeCompare(b.name));
	const media = Object.entries(formData).filter(([, value]) => !scalar(value)).map(([name]) => name).sort();
	return {
		prompt,
		negative,
		seed: effective.seed ?? formData.seed ?? null,
		variant: effective.selectedVariant ?? null,
		director: director ? { mode: director.mode ?? null } : null,
		variables: effective.variables ? Object.keys(effective.variables).length : 0,
		settings,
		media,
		edited: Object.keys(overrides || {}).length > 0
	};
}

export function elixirIdFromPath(pathname: string, route: string): string | null {
	const prefix = `/plugins/${route}`;
	if (!pathname.startsWith(prefix)) return null;
	const rest = pathname.slice(prefix.length).replace(/^\/+|\/+$/g, '');
	return rest && /^[A-Za-z0-9]+$/.test(rest) ? rest : null;
}

export function countLabel(count: number, singular: string, plural = `${singular}s`): string {
	return `${count} ${count === 1 ? singular : plural}`;
}

function same(left: unknown, right: unknown): boolean {
	return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
}

export function formOverrides(
	overrides: NodeOverrides | null | undefined,
	savedForm: Record<string, unknown> | null | undefined,
	baseline: Record<string, unknown> | null | undefined,
	published: Record<string, unknown> | null | undefined
): NodeOverrides {
	const next: Record<string, unknown> = { ...((overrides && overrides.formData) || {}) };
	const keys = new Set([...Object.keys(baseline || {}), ...Object.keys(published || {})]);
	for (const key of keys) {
		if (same(baseline?.[key], published?.[key])) continue;
		if (same(published?.[key], savedForm?.[key])) delete next[key];
		else next[key] = published?.[key] ?? null;
	}
	const result: NodeOverrides = { ...(overrides || {}) };
	if (Object.keys(next).length > 0) result.formData = next;
	else delete result.formData;
	return result;
}

export function pinText(definition: ElixirDefinition, nodeId: string, key: string, text: string): ElixirDefinition {
	const node = definition.nodes.find((item) => item.id === nodeId);
	if (!node || node.kind !== 'llm') return definition;
	return updateNode(definition, nodeId, { pinned: { ...(node.pinned ?? {}), [key]: text } });
}

export function unpinText(definition: ElixirDefinition, nodeId: string, key: string | null = null): ElixirDefinition {
	const node = definition.nodes.find((item) => item.id === nodeId);
	if (!node || node.kind !== 'llm') return definition;
	if (key === null) return updateNode(definition, nodeId, { pinned: {} });
	const { [key]: _, ...rest } = node.pinned ?? {};
	return updateNode(definition, nodeId, { pinned: rest });
}

export function writerTargets(definition: ElixirDefinition, presetIndex: PresetIndex): Array<{ id: string; label: string; prompt: boolean }> {
	return definition.nodes
		.filter((node) => node.kind === 'preset')
		.map((node) => ({ id: node.id, label: nodeLabel(node, presetIndex), prompt: !!presetIndex.get(presetKey(node.preset_id, node.mode))?.prompt }));
}
