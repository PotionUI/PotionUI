import type { APIResponse } from '$lib/types/api';

/** Who currently has this resource, from both grant paths. */
export interface AssignmentState {
	userIds: Set<string>;
	groupIds: Set<string>;
}

/**
 * Bridges AssignmentCard to one resource's assignment endpoints. The three
 * resources (preset/model/LLM config) shape their write endpoints differently
 * (pairs vs. arrays, direct vs. group-owned routes) - the adapter absorbs
 * that so the card only ever calls a uniform shape.
 */
export interface AssignmentAdapter {
	/** Used in copy: "Add {resourceLabel}", "assign this {resourceLabel} to...". */
	resourceLabel: string;
	loadState(): Promise<AssignmentState>;
	assignUser(userId: string): Promise<APIResponse>;
	unassignUser(userId: string): Promise<APIResponse>;
	assignGroup(groupId: string): Promise<APIResponse>;
	unassignGroup(groupId: string): Promise<APIResponse>;
}
