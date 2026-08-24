/**
 * The glyphs the editors draw inline.
 *
 * `$lib/utils/IconLibrary` carries none of the editing verbs (crop, scissors,
 * the four orientation marks), and these are drawn at 13-14px inside dense
 * control rows where an `<Icon>` box would fight the row height - so they are
 * raw `d` strings.
 */

export const EDITOR_ICONS = {
	crop: 'M6.13 1L6 16a2 2 0 002 2h15M1 6.13L16 6a2 2 0 012 2v15',
	scissors:
		'M9.879 14.121a3 3 0 10-4.243 4.243 3 3 0 004.243-4.243zm0 0L20 4M14.12 14.121a3 3 0 114.243 4.243 3 3 0 01-4.243-4.243zm0 0L4 4',
	frame:
		'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z',
	brush: 'M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z',
	rotateLeft: 'M3 10h10a5 5 0 015 5v1M3 10l5 5M3 10l5-5',
	rotateRight: 'M21 10H11a5 5 0 00-5 5v1M21 10l-5 5M21 10l-5-5',
	flipHorizontal: 'M12 3v18M8 7L4 12l4 5M16 7l4 5-4 5',
	flipVertical: 'M3 12h18M7 8l5-4 5 4M7 16l5 4 5-4',
	check: 'M5 13l4 4L19 7',
	stepBack: 'M15 19l-7-7 7-7M6 5v14',
	stepForward: 'M9 5l7 7-7 7M18 5v14',
	eraser: 'M4 20h16M6.5 17.5l-2-2a2 2 0 010-2.83l7.9-7.9a2 2 0 012.83 0l3.17 3.17a2 2 0 010 2.83L13.5 17.5z'
} as const;

export type EditorIconName = keyof typeof EDITOR_ICONS;
