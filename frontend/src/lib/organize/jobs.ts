import type { OrganizeJob } from '$lib/types/organize';

export function jobIsActive(job: Pick<OrganizeJob, 'status'>): boolean {
	return job.status === 'queued' || job.status === 'running';
}

export function jobPercent(job: Pick<OrganizeJob, 'processed' | 'total' | 'status'>): number {
	if (job.status === 'completed') return 100;
	if (!job.total) return 0;
	return Math.min(100, Math.max(0, Math.round((job.processed / job.total) * 100)));
}
