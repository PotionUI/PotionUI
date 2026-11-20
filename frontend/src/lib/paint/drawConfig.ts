export type DrawBackground = 'white' | 'black' | 'transparent';

export interface DrawConfig {
	width: number;
	height: number;
	background: DrawBackground;
	pen: string | null;
	scribble: boolean;
}

export interface DrawSizeChoice {
	label: string;
	width: number;
	height: number;
}

const BACKGROUNDS: readonly DrawBackground[] = ['white', 'black', 'transparent'];

function record(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function side(value: unknown): number | null {
	const n = typeof value === 'number' ? value : Number(value);
	if (!Number.isFinite(n) || n < 16 || n > 4096) return null;
	return Math.round(n);
}

export function readDrawConfig(
	config: unknown
): Partial<DrawConfig> & { size: [number, number] | null } {
	const root = record(config);
	const editor = record(root.editor ?? record(root.configuration).editor);
	const draw = record(editor.draw);
	const size = Array.isArray(draw.size) ? draw.size : null;
	const width = size ? side(size[0]) : null;
	const height = size ? side(size[1]) : null;
	const background = BACKGROUNDS.find((b) => b === draw.background);
	const pen =
		typeof draw.pen === 'string' && /^#[0-9a-fA-F]{3,6}$/.test(draw.pen) ? draw.pen : null;
	return {
		size: width && height ? [width, height] : null,
		background,
		pen,
		scribble: draw.scribble === false ? false : undefined
	};
}

export function drawSizeChoices(preset: [number, number] | null): DrawSizeChoice[] {
	const choices: DrawSizeChoice[] = [];
	if (preset) {
		choices.push({
			label: `Preset · ${preset[0]} × ${preset[1]}`,
			width: preset[0],
			height: preset[1]
		});
	}
	choices.push(
		{ label: '1024 × 1024', width: 1024, height: 1024 },
		{ label: '768 × 512', width: 768, height: 512 },
		{ label: '512 × 768', width: 512, height: 768 },
		{ label: '512 × 512', width: 512, height: 512 }
	);
	const seen = new Set<string>();
	return choices.filter((c) => {
		const key = `${c.width}x${c.height}`;
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

export function penForBackground(background: DrawBackground, configured: string | null): string {
	if (configured) return configured;
	return background === 'black' ? '#ffffff' : '#111111';
}
