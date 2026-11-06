import type { ModelRoot, ModelRootBinding, ModelRootsOverview } from '$lib/services/api/models';
import { modelTypePresentation } from '$lib/utils/modelPresentation';
import { bindingRefKey, bindingSupportsHeaderScan } from '$lib/model-roots/logic';
import { formatBytes, formatCount } from '$lib/utils/format';

export { formatBytes, formatCount };

export const typeLabel = (type: string) => modelTypePresentation(type).label;

export function rootFiles(root: ModelRoot): number {
	return root.bindings.reduce((n, b) => n + b.indexed_files + b.unindexed, 0);
}
export function rootBytes(root: ModelRoot): number {
	return root.bindings.reduce((n, b) => n + b.size_bytes, 0);
}
export function rootUnindexed(root: ModelRoot): number {
	return root.bindings.reduce((n, b) => n + b.unindexed, 0);
}
export function rootMissing(root: ModelRoot): ModelRootBinding[] {
	return root.bindings.filter((b) => !b.exists);
}
export function bindingFiles(b: ModelRootBinding): number {
	return b.indexed_files + b.unindexed;
}

export const STATE_LABEL = { online: 'Online', offline: 'Offline', unreadable: 'Unreadable' } as const;
export const STATE_COLOR = {
	online: 'rgb(var(--success))',
	offline: 'rgb(var(--danger))',
	unreadable: 'rgb(var(--warning))'
} as const;

export interface TypeFolder {
	root: ModelRoot;
	binding: ModelRootBinding;
	position: number;
}
export interface TypeRow {
	type: string;
	label: string;
	folder: string;
	folders: TypeFolder[];
	writeRootId: string | null;
	files: number;
	bytes: number;
	multi: boolean;
}

export function typeRows(overview: ModelRootsOverview): TypeRow[] {
	return overview.types.map((t) => {
		const folders: TypeFolder[] = [];
		t.order.forEach((rootId, position) => {
			const root = overview.roots.find((r) => r.id === rootId);
			if (!root) return;
			for (const binding of root.bindings.filter((b) => b.model_type === t.model_type)) folders.push({ root, binding, position });
		});
		return {
			type: t.model_type,
			label: typeLabel(t.model_type),
			folder: t.folder,
			folders,
			writeRootId: t.write_root_id,
			files: folders.reduce((n, f) => n + bindingFiles(f.binding), 0),
			bytes: folders.reduce((n, f) => n + f.binding.size_bytes, 0),
			multi: t.order.length > 1
		};
	});
}

export function agoText(iso: string | null): string {
	if (!iso) return 'never';
	const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
	if (s < 90) return 'just now';
	if (s < 3600) return `${Math.round(s / 60)} min ago`;
	if (s < 86400) return `${Math.round(s / 3600)} h ago`;
	return `${Math.round(s / 86400)} d ago`;
}

export const supportsHeaderScan = bindingSupportsHeaderScan;
export const bindingKey = bindingRefKey;

