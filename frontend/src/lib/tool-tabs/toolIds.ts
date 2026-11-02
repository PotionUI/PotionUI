export const IMAGE_TOOL_IDS = ['crop', 'resize', 'canvas', 'slice', 'paint', 'mask', 'effects', 'colorKey', 'subject'] as const;

export const VIDEO_TOOL_IDS = ['trim', 'split', 'crop', 'resize', 'speed', 'mute', 'volume', 'replace', 'effects', 'frame'] as const;

export type ImageToolId = (typeof IMAGE_TOOL_IDS)[number];

export type VideoToolId = (typeof VIDEO_TOOL_IDS)[number];

export type EditorKind = 'image' | 'video';

export const DEFAULT_ACTIVE_TOOL: Record<EditorKind, string> = { image: 'paint', video: 'trim' };

export const TERMINAL_TOOLS: readonly string[] = ['slice'];

export function toolIdsFor(kind: EditorKind): readonly string[] {
	return kind === 'image' ? IMAGE_TOOL_IDS : VIDEO_TOOL_IDS;
}

export function isActiveToolOf(kind: EditorKind, id: string | null | undefined): id is string {
	return !!id && toolIdsFor(kind).includes(id);
}
