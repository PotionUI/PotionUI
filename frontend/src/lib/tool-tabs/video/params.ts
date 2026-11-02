import type { ToolParam } from '$lib/media-tools/types';
import { deriveMediaSizeOptions } from '$lib/media/editors/mediaSizeOptions';

export function volumeParams(duration: number): ToolParam[] {
	const fadeMax = Math.max(0.5, Math.min(10, Math.floor(duration * 10) / 10));
	return [
		{
			name: 'unit',
			type: 'select',
			label: 'Level in',
			default: 'percent',
			options: [
				{ label: '%', value: 'percent' },
				{ label: 'dB', value: 'db' }
			]
		},
		{ name: 'percent', type: 'float', label: 'Level', min: 0, max: 400, step: 5, unit: '%', default: 100, when: { field: 'unit', equals: 'percent' } },
		{ name: 'db', type: 'float', label: 'Level', min: -60, max: 20, step: 0.5, unit: 'dB', default: 0, when: { field: 'unit', equals: 'db' } },
		{ name: 'fade_in', type: 'float', label: 'Fade in', min: 0, max: fadeMax, step: 0.1, unit: 's', default: 0 },
		{ name: 'fade_out', type: 'float', label: 'Fade out', min: 0, max: fadeMax, step: 0.1, unit: 's', default: 0 }
	];
}

export const SPEED_PARAMS: ToolParam[] = [
	{ name: 'speed', type: 'float', label: 'Speed', min: 0.25, max: 4, step: 0.05, unit: '×', default: 2 },
	{ name: 'keep_pitch', type: 'checkbox', label: 'Keep pitch', default: true }
];

export const REPLACE_PARAMS: ToolParam[] = [
	{ name: 'start', type: 'float', label: 'Start at', description: 'Skip into the track', min: 0, unit: 's', default: 0 },
	{ name: 'loop', type: 'checkbox', label: 'Loop to fit', default: true }
];

export function resizeParams(options: { label: string; short: number }[]): ToolParam[] {
	return [
		{
			name: 'short',
			type: 'select',
			label: 'Size',
			options: options.map((option) => ({ label: option.label, value: option.short }))
		},
		{
			name: 'quality',
			type: 'select',
			label: 'Quality',
			default: 'balanced',
			options: [
				{ label: 'High', value: 'high' },
				{ label: 'Balanced', value: 'balanced' },
				{ label: 'Small', value: 'small' }
			]
		}
	];
}

export function resizeOptions(size: { width: number; height: number }): { label: string; short: number }[] {
	const seen = new Set<number>();
	const out: { label: string; short: number }[] = [];
	for (const option of deriveMediaSizeOptions(size)) {
		const short = Math.min(option.width, option.height);
		if (seen.has(short)) continue;
		seen.add(short);
		out.push({ label: `${option.label} · ${option.width}×${option.height}`, short });
	}
	return out;
}
