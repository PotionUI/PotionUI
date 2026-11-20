export const IMAGE_TOOL_IDS = ['crop', 'resize', 'canvas', 'slice', 'paint', 'mask', 'effects', 'adjust', 'colorKey', 'subject'] as const;

export const VIDEO_TOOL_IDS = [
	'trim',
	'split',
	'speed',
	'crop',
	'resize',
	'mute',
	'volume',
	'replace_audio',
	'look',
	'lut',
	'fix_colors',
	'film_grain',
	'vignette',
	'ken_burns',
	'fade',
	'frame',
	'replace',
	'effects'
] as const;

export type ImageToolId = (typeof IMAGE_TOOL_IDS)[number];

export type VideoToolId = (typeof VIDEO_TOOL_IDS)[number];

export type EditorKind = 'image' | 'video';

export const DEFAULT_ACTIVE_TOOL: Record<EditorKind, string> = { image: 'paint', video: 'trim' };

export const TERMINAL_TOOLS: readonly string[] = ['slice'];

export function toolIdsFor(kind: EditorKind): readonly string[] {
	return kind === 'image' ? IMAGE_TOOL_IDS : VIDEO_TOOL_IDS;
}

export function isActiveToolOf(kind: EditorKind, id: string | null | undefined): id is string {
	return !!id && (toolIdsFor(kind).includes(id) || (kind === 'image' && id.startsWith('paint.')));
}
