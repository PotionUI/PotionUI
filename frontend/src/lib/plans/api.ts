import { api } from '$lib/services/api/index';
import type {
	GroupImpact,
	LimitKindDescriptor,
	Plan,
	PlanBody,
	PlansSettings,
	UserEffectiveLimits,
	UserUsageRow
} from './types';

interface Envelope<T> {
	success?: boolean;
	message?: string;
	data: T;
}

async function unwrap<T>(request: Promise<{ data: Envelope<T> | T }>): Promise<T> {
	const response = await request;
	const body = response.data as Envelope<T>;
	return body && typeof body === 'object' && 'data' in body ? body.data : (response.data as T);
}

const client = () => api.getClient();

export function listLimitKinds(): Promise<LimitKindDescriptor[]> {
	return unwrap(client().get('/api/plans/kinds'));
}

export function listPlans(): Promise<Plan[]> {
	return unwrap(client().get('/api/plans'));
}

export function createPlan(body: PlanBody): Promise<Plan> {
	return unwrap(client().post('/api/plans', body));
}

export function updatePlan(planId: string, body: PlanBody): Promise<Plan> {
	return unwrap(client().put(`/api/plans/${planId}`, body));
}

export function deletePlan(planId: string, reassignTo: string | null = null): Promise<void> {
	return unwrap(client().delete(`/api/plans/${planId}`, { params: reassignTo ? { reassign_to: reassignTo } : undefined }));
}

export function getPlansSettings(): Promise<PlansSettings> {
	return unwrap(client().get('/api/plans/settings'));
}

export function updatePlansSettings(patch: Partial<PlansSettings>): Promise<PlansSettings> {
	return unwrap(client().put('/api/plans/settings', patch));
}

export function setGroupPlan(groupId: string, planId: string | null): Promise<void> {
	return unwrap(client().put(`/api/plans/groups/${groupId}`, { plan_id: planId }));
}

export function getGroupPlanImpact(groupId: string, planId: string | null): Promise<GroupImpact> {
	return unwrap(client().get(`/api/plans/groups/${groupId}/impact`, { params: { plan_id: planId ?? '' } }));
}

export function setUserPlan(userId: string, planId: string | null): Promise<void> {
	return unwrap(client().put(`/api/plans/users/${userId}`, { plan_id: planId }));
}

export function getUserEffectiveLimits(userId: string): Promise<UserEffectiveLimits> {
	return unwrap(client().get(`/api/plans/users/${userId}/effective`));
}

export function listUsersUsage(): Promise<UserUsageRow[]> {
	return unwrap(client().get('/api/plans/usage'));
}
