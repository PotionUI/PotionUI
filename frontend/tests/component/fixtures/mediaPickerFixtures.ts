export function file(id: number, type: string, name: string, width?: number, height?: number) {
	return {
		id,
		width,
		height,
		file_path: `out/${name}`,
		file_type: type,
		is_final: true,
		created_at: '2026-01-01T00:00:00Z'
	};
}

export function generation(id: string, files: ReturnType<typeof file>[], extra: Record<string, unknown> = {}) {
	return {
		id,
		preset_name: `Preset ${id}`,
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: { prompt: `prompt of ${id}` },
		files,
		rating: 0,
		is_favorite: false,
		...extra
	};
}

export const GENERATIONS = [
	generation('g1', [file(1, 'image', 'a.png', 1024, 576), file(2, 'image', 'b.png', 576, 1024)]),
	generation('g2', [file(3, 'video', 'c.mp4', 1024, 576)]),
	generation('g3', [file(4, 'audio', 'd.wav')])
];

export function libraryItem(id: string, mediaType: string, name: string) {
	return {
		id,
		filename: `stored-${name}`,
		original_filename: name,
		media_type: mediaType,
		url: `/api/media/uploads/${name}`,
		width: 800,
		height: 600,
		size: 2048,
		created_at: '2026-02-03T00:00:00Z',
		tags: []
	};
}

export const LIBRARY = [
	libraryItem('l1', 'image', 'one.png'),
	libraryItem('l2', 'image', 'two.png'),
	libraryItem('l3', 'video', 'three.mp4')
];

export function kitItem(id: string, kitId: string, kind: 'image' | 'video' | 'audio' | 'text', name: string) {
	return {
		id,
		kit_id: kitId,
		kind,
		name,
		position: 0,
		media_ref: kind === 'text' ? null : `uploads/${name}`,
		text: kind === 'text' ? 'a note' : null,
		url: kind === 'text' ? null : `/api/media/uploads/${name}`,
		thumbnail_url: null,
		width: kind === 'image' || kind === 'video' ? 640 : null,
		height: kind === 'image' || kind === 'video' ? 480 : null,
		duration_seconds: kind === 'video' || kind === 'audio' ? 3 : null,
		missing: false,
		created_at: '2026-01-01T00:00:00Z'
	};
}

export function kit(id: string, name: string, items: ReturnType<typeof kitItem>[], label: string | null = 'Actor') {
	const counts: Record<string, number> = {};
	for (const item of items) counts[item.kind] = (counts[item.kind] ?? 0) + 1;
	return {
		id,
		name,
		handle: name.toLowerCase().replace(/\s+/g, '-'),
		label,
		position: 0,
		item_count: items.length,
		counts,
		preview_items: items.slice(0, 3),
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

export const KIT_ITEMS_A = [
	kitItem('ki1', 'kt_a', 'image', 'face-a.png'),
	kitItem('ki2', 'kt_a', 'image', 'face-b.png'),
	kitItem('ki3', 'kt_a', 'video', 'clip.mp4'),
	kitItem('ki4', 'kt_a', 'text', 'note')
];
export const KIT_ITEMS_B = [kitItem('ki5', 'kt_b', 'audio', 'voice.wav'), kitItem('ki6', 'kt_b', 'text', 'script')];
export const KIT_A = kit('kt_a', 'Hero', KIT_ITEMS_A);
export const KIT_B = kit('kt_b', 'Narrator', KIT_ITEMS_B, 'Voice');
