/**
 * Generation-related type definitions.
 * These types mirror the backend generation data structures.
 */

export interface GenerationParamModel {
	name: string;
	type: string;
	weight?: number;
}

/** Workspace as stored in the API. */
export interface Workspace {
	id: string;
	name: string;
	data: WorkspaceData;
	created_at: string;
	updated_at: string;
}

export interface WorkspaceData {
	tabs: WorkspaceTab[];
	activeTabId?: string;
}

export interface WorkspaceTab {
	name: string;
	color?: string | null;
	preset_id?: string | null;
	mode?: string | null;
	autoTagIds?: string[];
	autoCollectionIds?: string[];
}
