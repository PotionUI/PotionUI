/**
 * Generation-related type definitions.
 * These types mirror the backend generation data structures.
 */

import type { MediaRef } from '$lib/types/tabs';

export interface GenerationParamModel {
	name: string;
	type: string;
	weight?: number;
	filename?: string;
	model_type?: string;
}

export interface GenerationParameterMeta {
	label?: string;
	type?: string;
	unit?: string;
}

export interface GenerationParamsResponse {
	generation_id?: string;
	index?: number;
	parameters: Record<string, unknown>;
	models: GenerationParamModel[];
	parameter_meta?: Record<string, GenerationParameterMeta>;
	parameter_order?: string[];
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
	key?: string | null;
	preset_id?: string | null;
	mode?: string | null;
	autoTagIds?: string[];
	autoCollectionIds?: string[];
}
