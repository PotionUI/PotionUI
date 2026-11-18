import type { EffectTool } from '$lib/effects/catalog';
import type { ClipInfo, VideoParams } from './videoSteps';

export const SCENARIO_CLIP: ClipInfo = { width: 1280, height: 720, duration: 6.4 };

function effect(id: string, group: string, params: EffectTool['params']): EffectTool {
	return {
		id,
		label: id,
		icon: 'editor-effects',
		description: '',
		params,
		core: true,
		block: { group, time: 'none', shader: { url: `/s/${id}`, etag: 'e' }, uniforms: [], textures: [], derive: {}, gain: null, live: null, analysis: false, analysisKeys: null }
	};
}

export const SCENARIO_EFFECTS: EffectTool[] = [
	effect('look', 'colour', [
		{ name: 'look', type: 'look', label: 'Look', default: '', options: [{ value: '', label: 'None' }, { value: 'ember', label: 'Ember', group: 'colour' }] as never },
		{ name: 'strength', type: 'float', label: 'Strength', default: 1, min: 0, max: 1, step: 0.05 }
	]),
	effect('lut', 'colour', [
		{ name: 'lut', type: 'lut', label: 'LUT', default: '' },
		{ name: 'strength', type: 'float', label: 'Strength', default: 1, min: 0, max: 1, step: 0.05 }
	]),
	effect('fix_colors', 'colour', [
		{ name: 'white_balance', type: 'checkbox', label: 'White balance', default: true },
		{ name: 'contrast', type: 'checkbox', label: 'Normalise contrast', default: true },
		{ name: 'strength', type: 'float', label: 'Strength', default: 100, min: 0, max: 100, step: 5, unit: '%' }
	]),
	effect('film_grain', 'texture', [
		{ name: 'amount', type: 'float', label: 'Amount', default: 30, min: 0, max: 100, step: 1, unit: '%' },
		{ name: 'size', type: 'float', label: 'Size', default: 1.5, min: 1, max: 3, step: 0.1 },
		{ name: 'color', type: 'checkbox', label: 'Colour grain', default: false }
	]),
	effect('vignette', 'texture', [
		{ name: 'amount', type: 'float', label: 'Amount', default: 40, min: 0, max: 100, step: 1, unit: '%' },
		{ name: 'radius', type: 'float', label: 'Radius', default: 50, min: 0, max: 100, step: 1, unit: '%' },
		{ name: 'softness', type: 'float', label: 'Softness', default: 50, min: 0, max: 100, step: 1, unit: '%' }
	]),
	effect('ken_burns', 'motion', [
		{ name: 'zoom_from', type: 'float', label: 'Start zoom', default: 1, min: 1, max: 3, step: 0.05, unit: 'x' },
		{ name: 'zoom_to', type: 'float', label: 'End zoom', default: 1.25, min: 1, max: 3, step: 0.05, unit: 'x' },
		{ name: 'pan_from', type: 'anchor', label: 'Start at', default: 'center' },
		{ name: 'pan_to', type: 'anchor', label: 'End at', default: 'center' },
		{ name: 'easing', type: 'select', label: 'Easing', default: 'smooth', options: [{ label: 'Smooth', value: 'smooth' }, { label: 'Linear', value: 'linear' }] as never }
	]),
	effect('fade', 'transitions', [
		{ name: 'fade_in', type: 'float', label: 'Fade in', default: 1, min: 0, max: 10, step: 0.1, unit: 's' },
		{ name: 'fade_out', type: 'float', label: 'Fade out', default: 1, min: 0, max: 10, step: 0.1, unit: 's' },
		{ name: 'sound', type: 'checkbox', label: 'Fade the sound too', default: true }
	])
];

export interface ScenarioStep {
	op: string;
	params: VideoParams;
}

export interface Scenario {
	name: string;
	steps: ScenarioStep[];
}

export const SCENARIOS: Scenario[] = [
	{ name: 'trim', steps: [{ op: 'trim', params: { start: 0.8, end: 5.6 } }] },
	{ name: 'split', steps: [{ op: 'split', params: { at: 3.2 } }] },
	{ name: 'speed', steps: [{ op: 'speed', params: { speed: 1.25, keep_pitch: true } }] },
	{ name: 'speed-unpitched', steps: [{ op: 'speed', params: { speed: 0.5, keep_pitch: false } }] },
	{
		name: 'crop',
		steps: [{ op: 'crop', params: { rect: { x: 0.125, y: 0, width: 0.75, height: 1 }, rotation: 90, flipH: true, flipV: false } }]
	},
	{ name: 'resize', steps: [{ op: 'resize', params: { short: 480, quality: 'high' } }] },
	{ name: 'mute', steps: [{ op: 'mute', params: {} }] },
	{ name: 'volume-percent', steps: [{ op: 'volume', params: { unit: 'percent', percent: 140, db: 0, fade_in: 0.5, fade_out: 1 } }] },
	{ name: 'volume-db', steps: [{ op: 'volume', params: { unit: 'db', percent: 100, db: -6, fade_in: 0, fade_out: 0 } }] },
	{ name: 'replace-audio', steps: [{ op: 'replace_audio', params: { audio: { item_id: 'a-1' }, name: 'theme.mp3', start: 1.5, loop: false } }] },
	{ name: 'look', steps: [{ op: 'look', params: { look: 'ember', strength: 0.8 } }] },
	{ name: 'lut', steps: [{ op: 'lut', params: { lut: 'warm.cube', strength: 0.4 } }] },
	{ name: 'fix-colours', steps: [{ op: 'fix_colors', params: { white_balance: true, contrast: false, strength: 75 } }] },
	{ name: 'film-grain', steps: [{ op: 'film_grain', params: { amount: 55, size: 2, color: true } }] },
	{ name: 'vignette', steps: [{ op: 'vignette', params: { amount: 60, radius: 40, softness: 70 } }] },
	{ name: 'ken-burns', steps: [{ op: 'ken_burns', params: { zoom_from: 1, zoom_to: 1.5, pan_from: 'top-left', pan_to: 'bottom-right', easing: 'linear' } }] },
	{ name: 'fade', steps: [{ op: 'fade', params: { fade_in: 0.5, fade_out: 2, sound: false } }] },
	{
		name: 'stack',
		steps: [
			{ op: 'trim', params: { start: 0.8, end: 5.6 } },
			{ op: 'speed', params: { speed: 2, keep_pitch: true } },
			{ op: 'crop', params: { rect: { x: 0, y: 0, width: 0.5, height: 0.5 }, rotation: 0, flipH: false, flipV: false } },
			{ op: 'resize', params: { short: 360, quality: 'balanced' } },
			{ op: 'volume', params: { unit: 'percent', percent: 50, db: 0, fade_in: 0, fade_out: 0.5 } },
			{ op: 'look', params: { look: 'ember', strength: 1 } },
			{ op: 'vignette', params: { amount: 40, radius: 50, softness: 50 } },
			{ op: 'split', params: { at: 1.1 } }
		]
	}
];
