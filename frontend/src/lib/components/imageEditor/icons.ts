import { EDITOR_ICONS } from '$lib/media/editors/editorIcons';

export const PAINT_ICONS = {
	brush:
		'M9.53 16.122a3 3 0 00-5.78 1.128 2.25 2.25 0 01-2.4 2.245 4.5 4.5 0 008.4-2.245c0-.399-.078-.78-.22-1.128zm0 0a15.998 15.998 0 003.388-1.62m-5.043-.025a15.994 15.994 0 011.622-3.395m3.42 3.42a15.995 15.995 0 004.764-4.648l3.876-5.814a1.151 1.151 0 00-1.597-1.597L14.146 6.32a15.996 15.996 0 00-4.649 4.763m3.42 3.42a6.776 6.776 0 00-3.42-3.42',
	eraser: EDITOR_ICONS.eraser,
	check: EDITOR_ICONS.check,
	undo: 'M9 14L4 9l5-5M4 9h10a6 6 0 010 12h-3',
	redo: 'M15 14l5-5-5-5M20 9H10a6 6 0 000 12h3',
	plus: 'M12 5v14M5 12h14',
	minus: 'M5 12h14',
	fit: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
	panel: 'M4 5h16v14H4zM15 5v14',
	full: 'M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3',
	move: 'M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20',
	transform: 'M7 7h10v10H7zM3 3h4v4H3zM17 3h4v4h-4zM3 17h4v4H3zM17 17h4v4h-4z',
	fill: 'M5 11l6-6 8 8-6 6a2 2 0 01-2.8 0L5 13.8A1 1 0 015 11zM11 5L8 2M20 16c0 0 2 2.2 2 3.5a2 2 0 11-4 0c0-1.3 2-3.5 2-3.5z',
	pick: 'M15 4l5 5M13.5 5.5l5 5-8.5 8.5H5v-5l8.5-8.5zM14 3.5a2.1 2.1 0 013 0l3.5 3.5a2.1 2.1 0 010 3',
	rect: 'M4 4h16v16H4z',
	lasso:
		'M12 4c4.4 0 8 1.7 8 4s-3.6 4-8 4-8-1.7-8-4 3.6-4 8-4zM7.5 12.5C6 15 7 18 10 18.5c1.5.2 2-.8 1.5-2',
	crop: 'M6.13 1L6 16a2 2 0 002 2h15M1 6.13L16 6a2 2 0 012 2v15',
	adjust: 'M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M14 4v4M8 10v4M16 16v4',
	layers: 'M12 3l9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 16l9 5 9-5',
	canvas: 'M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4M9 9h6v6H9z',
	eye: 'M2.5 12s3.5-7 9.5-7 9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7zM12 15a3 3 0 100-6 3 3 0 000 6z',
	eyeOff:
		'M3 3l18 18M10.6 5.2A9 9 0 0112 5c6 0 9.5 7 9.5 7a15 15 0 01-3.2 4M6.5 6.9C3.8 8.6 2.5 12 2.5 12S6 19 12 19a9 9 0 004-.9',
	trash:
		'M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16',
	merge: 'M6 4v4a6 6 0 006 6h0a6 6 0 006-6V4M12 14v6M9 17l3 3 3-3',
	duplicate: 'M8 8h11v11H8zM5 16V5h11',
	flipH: EDITOR_ICONS.flipHorizontal,
	flipV: EDITOR_ICONS.flipVertical,
	rotateLeft: EDITOR_ICONS.rotateLeft,
	rotateRight: EDITOR_ICONS.rotateRight,
	open: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z',
	addImage:
		'M4 15l4-4a2 2 0 013 0l3 3M13 12l1-1a2 2 0 013 0l3 3M4 7v10a2 2 0 002 2h12a2 2 0 002-2v-2M18 3v6M15 6h6',
	up: 'M5 15l7-7 7 7',
	down: 'M19 9l-7 7-7-7',
	pen: EDITOR_ICONS.brush,
	dots: 'M5 12h.01M12 12h.01M19 12h.01'
} as const;

export type PaintIconName = keyof typeof PAINT_ICONS;
