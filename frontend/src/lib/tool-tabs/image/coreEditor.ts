import { CORE_OWNER } from '$lib/registries/registry';
import { mediaToolCatalog } from '$lib/media-tools/catalog';
import type { ToolDTO } from '$lib/media-tools/types';
import { railGroups, railTools, type RailToolEntry } from '$lib/editors/railTools';
import { stepKinds, type StepKind } from '$lib/editors/stepKinds';
import { coreSizeAfter, coreStepPayload } from './payload';
import { PREVIEW_SIDE, coreApplyClientStep } from './render';
import CurvesPlotOptions from '$lib/components/tool-tabs/image/CurvesPlotOptions.svelte';
import { effectCardEntry, gradeEntry, liveEffectEntry, opSlidersEntry, toggleEntry } from './entrySchemas';
import { imageEffects } from './shaderEffect';
import { ensureGenerateEditor } from './generate/register';
import { ensurePaintEntries } from './paintEntries';
import { cropToolActions, sliceToolActions } from './canvasActions';
import { editorToolIcons } from '$lib/utils/iconGlyphs';
import { CORE_RAIL, CORE_TOOLS, editorCatalogTools, type CoreImageTool } from './steps';

function coreCanvasActions(id: string): Partial<RailToolEntry> {
	if (id === 'crop') return { canvasActions: cropToolActions };
	if (id === 'slice') return { canvasActions: sliceToolActions };
	return {};
}

const STEP_TOOLS: readonly CoreImageTool[] = ['crop', 'resize', 'canvas', 'slice', 'paint', 'effects', 'colorKey', 'subject'];
const PREVIEW_TOOLS: ReadonlySet<string> = new Set(['crop', 'resize', 'paint', 'effects', 'slice']);

function coreStepKind(id: CoreImageTool): StepKind {
	const meta = CORE_TOOLS[id];
	const kind: StepKind = {
		id,
		editor: 'image',
		label: meta.label,
		icon: meta.icon,
		summary: (_params, ctx) => ctx.step.summary,
		outputSize: (_params, ctx) => coreSizeAfter(ctx.step, ctx.input),
		payload: (_params, ctx) => coreStepPayload(ctx.step, ctx.input, ctx.baked)
	};
	if (meta.lane) kind.lane = meta.lane;
	if (PREVIEW_TOOLS.has(id)) kind.preview = (frame, _params, ctx) => coreApplyClientStep(frame, ctx.step, ctx.maxSide ?? PREVIEW_SIDE);
	return kind;
}

function catalogStepKind(tool: ToolDTO): StepKind {
	return {
		id: tool.id,
		editor: 'image',
		label: tool.label,
		icon: tool.icon || 'extension',
		overridable: true,
		summary: (_params, ctx) => ctx.step.summary,
		outputSize: (_params, ctx) => coreSizeAfter(ctx.step, ctx.input),
		payload: (_params, ctx) => coreStepPayload(ctx.step, ctx.input, ctx.baked)
	};
}

const catalogKinds = new Map<string, () => void>();

interface EntrySeed {
	id: string;
	group: string;
	label: string;
	icon: string;
	order: number;
	component: 'entry' | 'card';
}

export const GRADE_SEEDS: readonly EntrySeed[] = [
	{ id: 'light', group: 'look', label: 'Light', icon: editorToolIcons.light, order: 10, component: 'entry' },
	{ id: 'colour', group: 'look', label: 'Colour', icon: editorToolIcons.colour, order: 20, component: 'entry' },
	{ id: 'split_tone', group: 'look', label: 'Split tone', icon: editorToolIcons.split_tone, order: 30, component: 'entry' },
	{ id: 'curves', group: 'look', label: 'Curves', icon: editorToolIcons.curves, order: 40, component: 'entry' },
	{ id: 'lut', group: 'look', label: 'LUT', icon: editorToolIcons.lut, order: 50, component: 'card' },
	{ id: 'film_grain', group: 'fx', label: 'Film grain', icon: editorToolIcons.film_grain, order: 10, component: 'entry' },
	{ id: 'vignette', group: 'fx', label: 'Vignette', icon: editorToolIcons.vignette, order: 20, component: 'entry' },
	{ id: 'grayscale', group: 'fx', label: 'Grayscale', icon: editorToolIcons.grayscale, order: 30, component: 'entry' },
	{ id: 'invert', group: 'fx', label: 'Invert', icon: editorToolIcons.invert, order: 40, component: 'entry' }
];

function seedBody(seed: EntrySeed): Partial<RailToolEntry> {
	if (seed.id === 'film_grain' || seed.id === 'vignette') return liveEffectEntry(seed.id);
	if (seed.id === 'grayscale' || seed.id === 'invert') return toggleEntry(seed.id);
	if (seed.component === 'card') return effectCardEntry();
	if (seed.id === 'light' || seed.id === 'colour') return gradeEntry(seed.id);
	if (seed.id === 'split_tone') return opSlidersEntry('split_tone');
	return { ...opSlidersEntry('curves'), options: { component: CurvesPlotOptions } };
}

export const EFFECT_ENTRY_IDS: ReadonlySet<string> = new Set(['lut', 'film_grain', 'vignette']);

const effectEntries = new Map<string, () => void>();

function syncEffectEntries(catalog: readonly ToolDTO[]): void {
	const wanted = new Map(imageEffects(catalog).filter((effect) => !GRADE_SEEDS.some((seed) => seed.id === effect.id)).map((effect) => [effect.id, effect]));
	for (const [id, dispose] of effectEntries) {
		if (wanted.has(id)) continue;
		dispose();
		effectEntries.delete(id);
	}
	let order = 100;
	for (const [id, effect] of wanted) {
		order += 10;
		if (effectEntries.has(id) || railTools.get('image', id)) continue;
		effectEntries.set(
			id,
			railTools.register(
				{ id, editor: 'image', group: effect.block.group === 'colour' ? 'look' : 'fx', label: effect.label, icon: effect.icon || 'editor-effects', order, ...effectCardEntry() },
				CORE_OWNER
			)
		);
	}
}

function syncCatalogKinds(catalog: readonly ToolDTO[]): void {
	const wanted = new Map(editorCatalogTools(catalog).map((tool) => [tool.id, tool]));
	for (const [id, dispose] of catalogKinds) {
		if (wanted.has(id)) continue;
		dispose();
		catalogKinds.delete(id);
	}
	for (const [id, tool] of wanted) {
		if (catalogKinds.has(id) || stepKinds.get('image', id)?.pluginId) continue;
		catalogKinds.set(id, stepKinds.register(catalogStepKind(tool), CORE_OWNER));
	}
}

let registered = false;

export function ensureCoreImageEditor(): void {
	if (registered) return;
	registered = true;
	const orders: Record<string, number> = { shape: 100, look: 300, fx: 330, looks: 360, tools: 400 };
	CORE_RAIL.forEach((group) => {
		railGroups.register({ id: group.id, editor: 'image', label: group.label, order: orders[group.id], ...(group.fold === undefined ? {} : { fold: group.fold }) });
		group.tools.forEach((id, toolIndex) => {
			const meta = CORE_TOOLS[id];
			railTools.register({ id, editor: 'image', group: group.id, label: meta.label, icon: meta.icon, order: (toolIndex + 1) * 10, ...coreCanvasActions(id) });
		});
	});
	for (const seed of GRADE_SEEDS) {
		railTools.register({
			id: seed.id,
			editor: 'image',
			group: seed.group,
			label: seed.label,
			icon: seed.icon,
			order: seed.order,
			...seedBody(seed)
		});
	}
	for (const id of STEP_TOOLS) stepKinds.register(coreStepKind(id));
	mediaToolCatalog.subscribe(syncCatalogKinds);
	mediaToolCatalog.subscribe(syncEffectEntries);
	ensureGenerateEditor();
	ensurePaintEntries();
}
