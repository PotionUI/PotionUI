import { formatBytes } from '$lib/utils/format';
import { modelDisplayName } from '$lib/utils/modelDisplay';
import { mediaFileThumbnailUrl } from '$lib/utils/modelPreview';
import type { EntityKind } from '../types';
import { byName } from './shared';

export interface PickerModel {
	id: string;
	name?: string | null;
	custom_name?: string | null;
	filename?: string | null;
	model_type?: string | null;
	file_size?: number | null;
	location?: string | null;
	sha256?: string | null;
	copies?: unknown[] | null;
	files?: Array<{ file_type?: string; url?: string; thumbnail_medium?: string }> | null;
	providers?: Array<{ name?: string | null }> | null;
}

export function pickerModelName(model: PickerModel): string {
	return modelDisplayName(model) || model.id;
}

function previewSrc(model: PickerModel): string | null {
	const file = model.files?.find((f) => f.file_type === 'image' || f.file_type === 'thumbnail' || f.file_type === 'video');
	return file ? mediaFileThumbnailUrl(file) || null : null;
}

function sizeLabel(model: PickerModel): string {
	return model.file_size ? formatBytes(model.file_size, 1) : '';
}

export const modelsKind: EntityKind<PickerModel> = {
	id: 'models',
	singular: 'model',
	plural: 'models',
	icon: 'database',
	getId: (model) => model.id,
	getName: pickerModelName,
	searchText: (model) => [pickerModelName(model), model.filename, model.model_type, model.id].filter(Boolean).join(' '),
	lead: (model) => ({ type: 'thumb', src: previewSrc(model), icon: 'database' }),
	facts: [
		{ key: 'type', label: 'Type', value: (m) => m.model_type, as: 'tag', base: true },
		{
			key: 'filename',
			label: 'File',
			value: (m) => (m.filename && m.filename !== pickerModelName(m) ? m.filename : ''),
			as: 'text',
			base: true
		},
		{ key: 'folder', label: 'Folder', value: (m) => m.location, as: 'text' },
		{ key: 'size', label: 'Size', value: sizeLabel, as: 'text' },
		{ key: 'sha', label: 'Hash', value: (m) => (m.sha256 ? m.sha256.slice(0, 8) : ''), as: 'text' }
	],
	columns: [
		{ key: 'type', label: 'Type', width: '110px', mono: true, value: (m) => m.model_type ?? '' },
		{ key: 'size', label: 'Size', width: '80px', priority: 1, mono: true, align: 'right', value: sizeLabel },
		{ key: 'copies', label: 'Copies', width: '70px', priority: 1, mono: true, value: (m) => (m.copies?.length ? String(m.copies.length) : '') }
	],
	filters: [
		{
			key: 'type',
			label: 'Type',
			options: (rows) => [
				{ value: '', label: 'Any type' },
				...[...new Set(rows.map((m) => m.model_type).filter((t): t is string => !!t))]
					.sort()
					.map((value) => ({ value, label: value }))
			],
			match: (m, value) => m.model_type === value
		}
	],
	sorts: [
		{ value: 'name', label: 'Name', compare: byName(pickerModelName) },
		{ value: 'size', label: 'Largest', compare: (a, b) => (b.file_size ?? 0) - (a.file_size ?? 0) }
	],
	defaultSort: 'name',
	searchPlaceholder: 'Search name, file, type',
	empty: { title: 'No models indexed', description: 'Index a models folder first, then assign models here.' },
	rowHeight: 58
};
