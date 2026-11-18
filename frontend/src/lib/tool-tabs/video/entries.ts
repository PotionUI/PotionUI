import type { Component } from 'svelte';
import { CORE_OWNER } from '$lib/registries/registry';
import { railGroups, railTools, type EntryContext, type RailToolEntry } from '$lib/editors/railTools';
import type { FieldConfig } from '$lib/form/reactions';
import { mediaToolCatalog } from '$lib/media-tools/catalog';
import type { ToolDTO } from '$lib/media-tools/types';
import { toolParamsToSchema } from '$lib/media-tools/toolSchema';
import { effectTools, type EffectTool } from '$lib/effects/catalog';
import { editorToolIcons } from '$lib/utils/iconGlyphs';
import CropOptions from '$lib/components/tool-tabs/video/options/CropOptions.svelte';
import FrameOptions from '$lib/components/tool-tabs/video/options/FrameOptions.svelte';
import LooksOptions from '$lib/components/tool-tabs/video/options/LooksOptions.svelte';
import OutputReadout from '$lib/components/tool-tabs/video/options/OutputReadout.svelte';
import PreviewClip from '$lib/components/tool-tabs/video/options/PreviewClip.svelte';
import SplitOptions from '$lib/components/tool-tabs/video/options/SplitOptions.svelte';
import TrackPicker from '$lib/components/tool-tabs/video/options/TrackPicker.svelte';
import TrimOptions from '$lib/components/tool-tabs/video/options/TrimOptions.svelte';
import { REPLACE_PARAMS, SPEED_PARAMS, resizeParams, volumeParams } from './params';
import type { VideoToolHost, VideoView } from './toolHost';

export const VIDEO_EDITOR = 'video' as const;

export interface VideoGroupSeed {
	id: string;
	label: string;
	order: number;
	fold?: number;
}

export const VIDEO_GROUPS: readonly VideoGroupSeed[] = [
	{ id: 'time', label: 'Time', order: 100, fold: 40 },
	{ id: 'frame', label: 'Frame', order: 200, fold: 50 },
	{ id: 'sound', label: 'Sound', order: 300, fold: 30 },
	{ id: 'look', label: 'Look', order: 400, fold: 20 },
	{ id: 'effects', label: 'Effects', order: 500, fold: 10 },
	{ id: 'still', label: 'Still', order: 600 }
];

export const APPLY_LABELS: Readonly<Record<string, string>> = { trim: 'Apply trim', crop: 'Apply crop', mute: 'Mute' };

export const LIVE_ENTRIES: readonly string[] = ['look', 'lut', 'fix_colors', 'film_grain', 'vignette', 'ken_burns', 'fade'];

export const CHOICE_ENTRIES: readonly string[] = ['look', 'lut'];

export const ACTION_ENTRIES: readonly string[] = ['frame'];

type Body = Partial<RailToolEntry>;

function viewOf(ctx: EntryContext): VideoView {
	return (ctx.host as VideoToolHost).video;
}

function component(value: unknown): { component: Component<any> } {
	return { component: value as Component<any> };
}

function valueBody(op: string, schema: (view: VideoView) => FieldConfig[], normalize?: (values: Record<string, unknown>) => Record<string, unknown>): Body {
	return {
		schema: (ctx) => schema(viewOf(ctx)),
		values: (ctx) => viewOf(ctx).panelParams(op),
		onValues: (values, info, ctx) => {
			if (info.commit) return;
			viewOf(ctx).setParams(op, normalize ? normalize(values) : values);
		}
	};
}

function effectSchema(view: VideoView, id: string): FieldConfig[] {
	const effect = view.effects.get(id);
	if (!effect) return [];
	return toolParamsToSchema(id === 'look' ? effect.params.filter((param) => param.type !== 'look') : effect.params);
}

export function effectBody(id: string): Body {
	return {
		...valueBody(id, (view) => effectSchema(view, id)),
		description: (ctx) => viewOf(ctx).effects.get(id)?.description || (viewOf(ctx).effects.has(id) ? '' : 'This effect is not available right now.'),
		options: component(id === 'look' ? LooksOptions : PreviewClip)
	};
}

const CORE_BODIES: Record<string, Body> = {
	trim: { options: component(TrimOptions) },
	split: { options: component(SplitOptions) },
	speed: { ...valueBody('speed', () => toolParamsToSchema(SPEED_PARAMS)), options: component(OutputReadout) },
	crop: { options: component(CropOptions) },
	resize: valueBody(
		'resize',
		(view) => toolParamsToSchema(resizeParams(view.sizeOptions)),
		(values) => (values.short === undefined ? values : { ...values, short: Number(values.short) })
	),
	mute: { description: 'Removes the sound from the saved clip. Playback goes quiet so you can hear it.' },
	volume: { ...valueBody('volume', (view) => toolParamsToSchema(volumeParams(view.outputLength || view.clip.duration))), options: component(OutputReadout) },
	replace_audio: valueBody('replace_audio', () => toolParamsToSchema(REPLACE_PARAMS)),
	frame: { options: component(FrameOptions) }
};

export const LEADS: Readonly<Record<string, Component<any>>> = { replace_audio: TrackPicker as unknown as Component<any> };

export interface VideoEntrySeed {
	id: string;
	group: string;
	label: string;
	icon: string;
	order: number;
}

export const VIDEO_ENTRIES: readonly VideoEntrySeed[] = [
	{ id: 'trim', group: 'time', label: 'Trim', icon: editorToolIcons.trim, order: 10 },
	{ id: 'split', group: 'time', label: 'Split', icon: editorToolIcons.split, order: 20 },
	{ id: 'speed', group: 'time', label: 'Speed', icon: editorToolIcons.speed, order: 30 },
	{ id: 'crop', group: 'frame', label: 'Crop', icon: editorToolIcons.crop, order: 10 },
	{ id: 'resize', group: 'frame', label: 'Resize', icon: editorToolIcons.resize, order: 20 },
	{ id: 'mute', group: 'sound', label: 'Mute', icon: editorToolIcons.mute, order: 10 },
	{ id: 'volume', group: 'sound', label: 'Volume', icon: editorToolIcons.volume, order: 20 },
	{ id: 'replace_audio', group: 'sound', label: 'Replace audio', icon: editorToolIcons.audio, order: 30 },
	{ id: 'look', group: 'look', label: 'Looks', icon: editorToolIcons.looks, order: 10 },
	{ id: 'lut', group: 'look', label: 'LUT', icon: editorToolIcons.lut, order: 20 },
	{ id: 'fix_colors', group: 'look', label: 'Fix colours', icon: editorToolIcons.fix, order: 30 },
	{ id: 'film_grain', group: 'effects', label: 'Film grain', icon: editorToolIcons.film_grain, order: 10 },
	{ id: 'vignette', group: 'effects', label: 'Vignette', icon: editorToolIcons.vignette, order: 20 },
	{ id: 'ken_burns', group: 'effects', label: 'Ken Burns', icon: editorToolIcons.kenburns, order: 30 },
	{ id: 'fade', group: 'effects', label: 'Fade', icon: editorToolIcons.fade, order: 40 },
	{ id: 'frame', group: 'still', label: 'Extract frame', icon: editorToolIcons.frame, order: 10 }
];

const effectEntries = new Map<string, () => void>();

function wantedEffects(catalog: readonly ToolDTO[]): Map<string, EffectTool> {
	return new Map(effectTools(catalog).filter((effect) => !VIDEO_ENTRIES.some((seed) => seed.id === effect.id)).map((effect) => [effect.id, effect]));
}

export function syncVideoEffectEntries(catalog: readonly ToolDTO[]): void {
	const wanted = wantedEffects(catalog);
	for (const [id, dispose] of effectEntries) {
		if (wanted.has(id)) continue;
		dispose();
		effectEntries.delete(id);
	}
	let order = 100;
	for (const [id, effect] of wanted) {
		order += 10;
		if (effectEntries.has(id) || railTools.get(VIDEO_EDITOR, id)) continue;
		effectEntries.set(
			id,
			railTools.register(
				{ id, editor: VIDEO_EDITOR, group: effect.block.group === 'colour' ? 'look' : 'effects', label: effect.label, icon: effect.icon || 'editor-effects', order, ...effectBody(id) },
				CORE_OWNER
			)
		);
	}
}

let registered = false;

export function ensureCoreVideoEditor(): void {
	if (registered) return;
	registered = true;
	for (const group of VIDEO_GROUPS) railGroups.register({ id: group.id, editor: VIDEO_EDITOR, label: group.label, order: group.order, ...(group.fold === undefined ? {} : { fold: group.fold }) });
	for (const seed of VIDEO_ENTRIES) {
		const body = LIVE_ENTRIES.includes(seed.id) ? effectBody(seed.id) : CORE_BODIES[seed.id];
		railTools.register({ id: seed.id, editor: VIDEO_EDITOR, group: seed.group, label: seed.label, icon: seed.icon, order: seed.order, ...body });
	}
	mediaToolCatalog.subscribe(syncVideoEffectEntries);
}
