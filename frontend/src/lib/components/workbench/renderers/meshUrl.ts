/**
 * Resolves a displayable mesh URL and its geometry metadata out of the
 * loosely-typed object `Workbench.svelte`'s generic `workbench.file`
 * fallback branch hands a renderer (`file={currentGalleryItem ??
 * currentGeneration}` - shape depends on whether the panel is showing a
 * gallery-scrub item or the live generation).
 *
 * The two shapes differ, mirroring the video convention rather than audio's:
 * - Live generation (`workbenchUpdate.ts`): `current_mesh` is a bare URL
 *   string, geometry metadata is nested under `mesh_metadata` - same split
 *   as `current_video` / `video_metadata`.
 * - Gallery/history item: `url`/`originalUrl` plus flat `vertex_count` /
 *   `face_count` / `mesh_name` / `mesh_format`, matching the
 *   ImageData/VideoData/AudioData convention (metadata alongside the URL,
 *   not nested).
 *
 * No base-URL rewriting is applied in either case, matching how
 * `audioTracks` in `Workbench.svelte` reads `url`/`originalUrl` off a raw
 * file object for the audio renderer.
 */
export interface MeshMetadataLike {
	vertex_count?: number | null;
	face_count?: number | null;
	filename?: string | null;
	format?: string | null;
}

export interface MeshFileLike {
	current_mesh?: string | null;
	mesh_metadata?: MeshMetadataLike | null;
	url?: string | null;
	originalUrl?: string | null;
	vertex_count?: number | null;
	face_count?: number | null;
	mesh_name?: string | null;
	mesh_format?: string | null;
}

export function resolveMeshUrl(file: MeshFileLike | null | undefined): string | null {
	if (!file) return null;
	return file.current_mesh || file.originalUrl || file.url || null;
}

export interface ResolvedMeshMetadata {
	vertexCount: number | null;
	faceCount: number | null;
	filename: string | null;
	format: string | null;
}

export function resolveMeshMetadata(file: MeshFileLike | null | undefined): ResolvedMeshMetadata {
	const meta = file?.mesh_metadata;
	return {
		vertexCount: meta?.vertex_count ?? file?.vertex_count ?? null,
		faceCount: meta?.face_count ?? file?.face_count ?? null,
		filename: meta?.filename ?? file?.mesh_name ?? null,
		format: meta?.format ?? file?.mesh_format ?? null
	};
}

/**
 * Lowercase container format for a mesh file (download extension, URL
 * building). Falls back to 'glb' only when the server genuinely sent no
 * format - never a substitute for a real value.
 */
export function resolveMeshFormat(file: MeshFileLike | null | undefined): string {
	return (resolveMeshMetadata(file).format || 'glb').toLowerCase();
}
