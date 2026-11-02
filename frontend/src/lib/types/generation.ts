/**
 * Generation-related type definitions.
 * These types mirror the backend generation data structures.
 */

import type { MediaRef } from '$lib/types/tabs';

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
	kind?: 'generation' | 'tool';
	name: string;
	color?: string | null;
	tool?: string;
	source?: MediaRef | null;
	state?: unknown;
	activeTool?: string | null;
	renamed?: boolean;
	preset_id?: string | null;
	mode?: string | null;
	autoTagIds?: string[];
	autoCollectionIds?: string[];
}
