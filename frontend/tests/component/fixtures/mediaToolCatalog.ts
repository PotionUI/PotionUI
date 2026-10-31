import type { ToolDTO } from '$lib/media-tools/types';

export function toolDto(overrides: Partial<ToolDTO> & { id: string }): ToolDTO {
	return {
		label: overrides.id,
		icon: 'sliders',
		accepts: ['image'],
		produces: 'same',
		cardinality: 'one_to_one',
		params: [],
		requires: {},
		selection: null,
		scopes: ['library', 'history', 'field'],
		editor: null,
		preview: false,
		component: null,
		runnable: true,
		source: 'core',
		last_params: null,
		...overrides
	};
}

export const CORE_TOOLS: ToolDTO[] = [
	toolDto({ id: 'trim', label: 'Trim', icon: 'scissors', accepts: ['video', 'audio'], editor: 'trim' }),
	toolDto({ id: 'crop', label: 'Crop', icon: 'crop', accepts: ['image', 'video'], editor: 'crop' }),
	toolDto({ id: 'resize', label: 'Resize', icon: 'shrink', accepts: ['image', 'video'], editor: 'resize' }),
	toolDto({ id: 'rotate', label: 'Rotate', icon: 'refresh', accepts: ['image', 'video'], preview: true }),
	toolDto({ id: 'flip', label: 'Flip', icon: 'refresh', accepts: ['image', 'video'], preview: true }),
	toolDto({ id: 'speed', label: 'Speed', icon: 'gauge', accepts: ['video', 'audio'] }),
	toolDto({ id: 'fit_canvas', label: 'Fit canvas', icon: 'expand', accepts: ['image'], preview: true }),
	toolDto({ id: 'color_key', label: 'Color key', icon: 'palette', accepts: ['image'], preview: true }),
	toolDto({
		id: 'crop_subject',
		label: 'Crop to subject',
		icon: 'crop',
		accepts: ['image'],
		requires: { has_alpha: true }
	}),
	toolDto({ id: 'frame', label: 'Frame', icon: 'photo', accepts: ['video'], produces: 'image', editor: 'frame' }),
	toolDto({
		id: 'split',
		label: 'Split',
		icon: 'split',
		accepts: ['audio'],
		cardinality: 'one_to_many',
		editor: 'split'
	}),
	toolDto({
		id: 'concat',
		label: 'Concat',
		icon: 'film',
		accepts: ['video'],
		cardinality: 'many_to_one',
		selection: { min: 2, max: 8 }
	})
];

export const FIX_COLORS_TOOL: ToolDTO = toolDto({
	id: 'fix_colors',
	label: 'Fix colours',
	icon: 'palette',
	accepts: ['video'],
	group: 'effects'
});

export const A1111_TOOL: ToolDTO = toolDto({
	id: 'a1111-metadata-export.export-zip',
	label: 'Download with A1111 metadata',
	icon: 'download',
	accepts: ['image'],
	selection: { min: 1 },
	scopes: ['history'],
	component: 'plugin:a1111-metadata-export:A1111ExportTool.js',
	runnable: false,
	source: 'a1111-metadata-export'
});

export const BOOMERANG_TOOL: ToolDTO = toolDto({
	id: 'boomerang.loop',
	label: 'Boomerang',
	icon: 'refresh',
	accepts: ['video'],
	params: [{ name: 'repeats', type: 'integer', min: 1, max: 8, default: 2 }],
	scopes: ['library', 'history'],
	source: 'boomerang'
});
