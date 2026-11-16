import { CORE_OWNER } from '$lib/registries/registry';
import { mediaToolCatalog } from '$lib/media-tools/catalog';
import type { ToolDTO } from '$lib/media-tools/types';
import { railGroups, railTools } from '$lib/editors/railTools';
import { stepKinds, type StepKind } from '$lib/editors/stepKinds';
import { coreSizeAfter, coreStepPayload } from './payload';
import { PREVIEW_SIDE, coreApplyClientStep } from './render';
import { CORE_RAIL, CORE_TOOLS, editorCatalogTools, type CoreImageTool } from './steps';

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
	CORE_RAIL.forEach((group, groupIndex) => {
		railGroups.register({ id: group.id, editor: 'image', label: group.label, order: (groupIndex + 1) * 100 });
		group.tools.forEach((id, toolIndex) => {
			const meta = CORE_TOOLS[id];
			railTools.register({ id, editor: 'image', group: group.id, label: meta.label, icon: meta.icon, order: (toolIndex + 1) * 10 });
		});
	});
	for (const id of STEP_TOOLS) stepKinds.register(coreStepKind(id));
	mediaToolCatalog.subscribe(syncCatalogKinds);
}
