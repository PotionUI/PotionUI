import { api } from '$lib/services/api/index';
import type {
	GroupImpact,
	GroupPlanRow,
	LimitKindDescriptor,
	Plan,
	PlanBody,
	PlanDetail,
	PlansOverview,
	PlansSettings,
	UserPlanDetail,
	UsersUsage
} from './types';

interface Envelope<T> {
	success?: boolean;
	data: T;
}

async function unwrap<T>(request: Promise<{ data: Envelope<T> }>): Promise<T> {
	const response = await request;
	return response.data.data;
}

const client = () => api.getClient();
const BASE = '/api/admin/plans';

export interface DeleteResult {
	deleted: boolean;
	reassigned: { groups: number; users: number };
}

export function getPlansOverview(): Promise<PlansOverview> {
	return unwrap(client().get(BASE));
}

export async function listLimitKinds(): Promise<LimitKindDescriptor[]> {
	const data = await unwrap<{ kinds: LimitKindDescriptor[] }>(client().get(`${BASE}/kinds`));
	return data.kinds;
}

export function getPlanDetail(planId: string): Promise<PlanDetail> {
	return unwrap(client().get(`${BASE}/${planId}`));
}

export function createPlan(body: PlanBody): Promise<Plan> {
	return unwrap(client().post(BASE, body));
}

export function updatePlan(planId: string, body: PlanBody): Promise<Plan> {
	return unwrap(client().put(`${BASE}/${planId}`, body));
}

export function deletePlan(planId: string, reassignTo?: string): Promise<DeleteResult> {
	return unwrap(client().delete(`${BASE}/${planId}`, { params: reassignTo ? { reassign_to: reassignTo } : undefined }));
}

export function updatePlansSettings(patch: Partial<PlansSettings>): Promise<PlansSettings> {
	return unwrap(client().put(`${BASE}/settings`, patch));
}

export async function listGroupPlans(): Promise<GroupPlanRow[]> {
	const data = await unwrap<{ groups: GroupPlanRow[] }>(client().get(`${BASE}/groups`));
	return data.groups;
}

export function setGroupPlan(groupId: string, planId: string | null): Promise<void> {
	return unwrap(client().put(`${BASE}/groups/${groupId}`, { plan_id: planId }));
}

export function getGroupPlanImpact(groupId: string, planId: string | null): Promise<GroupImpact> {
	return unwrap(client().get(`${BASE}/groups/${groupId}/impact`, { params: { plan_id: planId ?? '' } }));
}

export function getUserPlanDetail(userId: string): Promise<UserPlanDetail> {
	return unwrap(client().get(`${BASE}/users/${userId}`));
}

export function setUserPlan(userId: string, planId: string | null): Promise<UserPlanDetail> {
	return unwrap(client().put(`${BASE}/users/${userId}`, { plan_id: planId }));
}

export function listUsersUsage(): Promise<UsersUsage> {
	return unwrap(client().get(`${BASE}/users`, { params: { limit: 500, sort: 'username' } }));
}
