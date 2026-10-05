import { isAxiosError } from 'axios';
import type { OrganizeProblem } from '$lib/types/organize';

export interface OrganizeErrorInfo {
	code: string | null;
	message: string;
	problems: OrganizeProblem[];
	jobId: string | null;
}

export function parseOrganizeError(err: unknown, fallback = 'Something went wrong. Try again.'): OrganizeErrorInfo {
	if (isAxiosError(err)) {
		const detail = (err.response?.data as { detail?: Record<string, unknown> } | undefined)?.detail;
		if (detail && typeof detail === 'object') {
			return {
				code: typeof detail.error === 'string' ? detail.error : null,
				message: typeof detail.message === 'string' && detail.message ? detail.message : fallback,
				problems: Array.isArray(detail.problems) ? (detail.problems as OrganizeProblem[]) : [],
				jobId: typeof detail.job_id === 'string' ? detail.job_id : null
			};
		}
	}
	return { code: null, message: fallback, problems: [], jobId: null };
}

export function problemMessage(info: OrganizeErrorInfo): string {
	if (info.problems.length === 0) return info.message;
	return info.problems.map((p) => p.message).join(' ');
}
